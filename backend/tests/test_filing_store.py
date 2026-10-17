"""Document rows for primary filings. Postgres tests roll back."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from titan.common.session import create_db_engine, create_session_factory
from titan.common.settings import SecSettings
from titan.ingest.errors import FilingConflictError
from titan.ingest.identifiers import archive_document_url, submissions_index_url
from titan.ingest.sec_transport import SecHttpClient
from titan.ingest.store import ingest_primary_filings, store_primary_documents
from titan.models import Company, Document
from titan.pit.repository import PointInTimeRepository

_UA = "Titan Research titan@example.com"
_RAW = b"<DOCUMENT>\r\n<TYPE>10-K\r\n\xff"
_AMENDMENT = b"<DOCUMENT>\r\n<TYPE>10-K/A\r\n"
_AS_OF = datetime(9999, 1, 1, tzinfo=UTC)


@pytest.fixture
def db_session() -> Iterator[Session]:
    engine = create_db_engine()
    try:
        with engine.connect() as connection:
            found = connection.execute(text("SELECT to_regclass('public.documents')")).scalar_one()
    except SQLAlchemyError:
        engine.dispose()
        pytest.skip("Postgres is not available")
    if found is None:
        engine.dispose()
        pytest.skip("documents table is not migrated")
    session = create_session_factory(engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


class _Routed:
    def __init__(self, routes: dict[str, bytes]) -> None:
        self.routes = routes

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = self.routes.get(str(request.url))
        if body is None:
            return httpx.Response(404, request=request)
        return httpx.Response(200, content=body, request=request)


def _cik() -> str:
    value = f"{uuid4().int % 10**10:010d}"
    return "0000000001" if value == "0000000000" else value


def _payload(
    cik: str,
    form: str,
    suffix: str,
    primary: str,
    acceptance: str,
    filed: str,
) -> dict[str, str]:
    return {
        "accessionNumber": f"{cik}-19-{suffix}",
        "filingDate": filed,
        "reportDate": "2019-01-27",
        "acceptanceDateTime": acceptance,
        "form": form,
        "primaryDocument": primary,
    }


def _index(cik: str, rows: list[dict[str, str]]) -> bytes:
    keys = (
        "accessionNumber",
        "filingDate",
        "reportDate",
        "acceptanceDateTime",
        "form",
        "primaryDocument",
    )
    columns = {key: [row[key] for row in rows] for key in keys}
    return json.dumps(
        {
            "cik": cik,
            "name": "Fixture Co",
            "tickers": ["FIX"],
            "filings": {"recent": columns, "files": []},
        }
    ).encode()


def _rows(cik: str) -> list[dict[str, str]]:
    return [
        _payload(
            cik,
            "10-K",
            "000023",
            "annual.htm",
            "2019-02-22T02:37:18.123Z",
            "2019-02-21",
        ),
        _payload(
            cik,
            "10-K/A",
            "000099",
            "annual-amend.htm",
            "2019-11-02T01:00:00.000Z",
            "2019-11-01",
        ),
    ]


_BODIES = {
    "annual.htm": _RAW,
    "annual-amend.htm": _AMENDMENT,
    "quarter.htm": b"<DOCUMENT>\n<TYPE>10-Q\n",
}


def _client(tmp_path: Path, cik: str, rows: list[dict[str, str]]) -> SecHttpClient:
    routes: dict[str, bytes] = {submissions_index_url(cik): _index(cik, rows)}
    for row in rows:
        primary = row["primaryDocument"]
        routes[archive_document_url(cik, row["accessionNumber"], primary)] = _BODIES[primary]
    settings = SecSettings(sec_user_agent=_UA, sec_cache_dir=tmp_path)
    return SecHttpClient(settings, transport=httpx.MockTransport(_Routed(routes)))


def _count(session: Session, company_id: UUID) -> int:
    value = session.scalar(
        select(func.count()).select_from(Document).where(Document.company_id == company_id)
    )
    assert value is not None
    return int(value)


def test_ingest_is_idempotent_and_keeps_amendments(
    db_session: Session,
    tmp_path: Path,
) -> None:
    cik = _cik()
    client = _client(tmp_path, cik, _rows(cik))
    try:
        first = ingest_primary_filings(db_session, client, cik=cik, since=date(2019, 1, 1))
        count = _count(db_session, first.company_id)
        second = ingest_primary_filings(db_session, client, cik=cik, since=date(2019, 1, 1))
        duplicated = store_primary_documents(
            db_session,
            first.company_id,
            first.documents + first.documents,
        )
    finally:
        client.close()

    assert count == 2
    assert _count(db_session, first.company_id) == 2
    assert [item.inserted for item in first.stored] == [True, True]
    assert [item.inserted for item in second.stored] == [False, False]
    assert [item.inserted for item in duplicated] == [False, False]
    assert [item.document_id for item in second.stored] == [
        item.document_id for item in first.stored
    ]
    visible = PointInTimeRepository(db_session).visible_documents(
        company_id=first.company_id,
        as_of=_AS_OF,
    )
    assert [(item.form_type, item.accession_number) for item in visible] == [
        ("10-K", f"{cik}-19-000023"),
        ("10-K/A", f"{cik}-19-000099"),
    ]
    original = visible[0]
    assert original.known_at == datetime(2019, 2, 21, 21, 37, 18, 123_000, tzinfo=UTC)
    assert original.known_at != datetime(2019, 2, 22, 2, 37, 18, 123_000, tzinfo=UTC)
    assert original.filed_at == datetime(2019, 2, 21, tzinfo=UTC)
    assert original.period_of_report == date(2019, 1, 27)
    stored = first.stored[0]
    assert stored.acceptance_original == "2019-02-22T02:37:18.123Z"
    assert stored.acceptance_precision == 3
    assert stored.sha256 == hashlib.sha256(_RAW).hexdigest()
    assert first.documents[0].body == _RAW
    cached = client.cache.load(first.documents[0].filing.archive_url)
    assert cached is not None
    assert cached.body == _RAW
    assert cached.sha256 == stored.sha256
    company = db_session.scalar(select(Company).where(Company.id == first.company_id))
    assert company is not None
    assert company.name == "Fixture Co"
    assert company.ticker == "FIX"


def test_existing_company_name_is_left_in_place(db_session: Session, tmp_path: Path) -> None:
    cik = _cik()
    db_session.add(Company(cik=cik, name="Old Name", ticker="OLD"))
    db_session.flush()
    client = _client(tmp_path, cik, _rows(cik)[:1])
    try:
        result = ingest_primary_filings(db_session, client, cik=cik, since=date(2019, 1, 1))
    finally:
        client.close()
    company = db_session.scalar(select(Company).where(Company.id == result.company_id))
    assert company is not None
    assert company.name == "Old Name"
    assert company.ticker == "OLD"


def test_conflicting_accession_does_not_overwrite(db_session: Session, tmp_path: Path) -> None:
    cik = _cik()
    client = _client(tmp_path, cik, _rows(cik)[:1])
    other_rows = [
        _payload(
            cik,
            "10-Q",
            "000023",
            "quarter.htm",
            "2019-05-17T00:22:47.000Z",
            "2019-05-16",
        )
    ]
    other = _client(tmp_path / "other", cik, other_rows)
    try:
        first = ingest_primary_filings(db_session, client, cik=cik, since=date(2019, 1, 1))
        with pytest.raises(FilingConflictError):
            ingest_primary_filings(db_session, other, cik=cik, since=date(2019, 1, 1))
    finally:
        client.close()
        other.close()
    visible = PointInTimeRepository(db_session).visible_documents(
        company_id=first.company_id,
        as_of=_AS_OF,
    )
    assert [(item.form_type, item.known_at) for item in visible] == [
        ("10-K", datetime(2019, 2, 21, 21, 37, 18, 123_000, tzinfo=UTC))
    ]


def test_same_accession_for_another_company_is_rejected(
    db_session: Session,
    tmp_path: Path,
) -> None:
    first_cik = _cik()
    second_cik = _cik()
    while second_cik == first_cik:
        second_cik = _cik()
    shared = f"{first_cik}-19-000023"
    first_client = _client(tmp_path, first_cik, _rows(first_cik)[:1])
    second_row = _payload(
        second_cik,
        "10-K",
        "000023",
        "annual.htm",
        "2019-02-22T02:37:18.123Z",
        "2019-02-21",
    )
    second_row["accessionNumber"] = shared
    second_client = _client(tmp_path / "second", second_cik, [second_row])
    try:
        first = ingest_primary_filings(
            db_session,
            first_client,
            cik=first_cik,
            since=date(2019, 1, 1),
        )
        with pytest.raises(FilingConflictError):
            ingest_primary_filings(
                db_session,
                second_client,
                cik=second_cik,
                since=date(2019, 1, 1),
            )
    finally:
        first_client.close()
        second_client.close()
    visible = PointInTimeRepository(db_session).visible_documents(
        company_id=first.company_id,
        as_of=_AS_OF,
    )
    assert [item.accession_number for item in visible] == [shared]
    assert _count(db_session, first.company_id) == 1
