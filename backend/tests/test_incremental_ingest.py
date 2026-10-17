"""Incremental ingest counts, isolated failures, and restart-safe upserts.

These tests use recorded submissions fixtures. They do not call SEC and they
do not download filings that fall outside ``since`` or ``limit``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from titan.common.session import create_db_engine, create_session_factory
from titan.common.settings import SecSettings
from titan.ingest.errors import SecSubmissionError
from titan.ingest.identifiers import (
    archive_document_url,
    older_submissions_url,
    submissions_index_url,
)
from titan.ingest.run import IngestCounts, IngestFailure, ingest_incrementally
from titan.ingest.sec_transport import SecHttpClient
from titan.models import Company, Document
from titan.pit.repository import PointInTimeRepository

_UA = "Titan Research titan@example.com"
_AS_OF = datetime(9999, 1, 1, tzinfo=UTC)
_SINCE = date(2020, 1, 1)
_COLUMNS = (
    "accessionNumber",
    "filingDate",
    "reportDate",
    "acceptanceDateTime",
    "form",
    "primaryDocument",
)


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
        self.urls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.urls.append(url)
        body = self.routes.get(url)
        if body is None:
            return httpx.Response(404, request=request)
        return httpx.Response(200, content=body, request=request)


def _cik() -> str:
    value = f"{uuid4().int % 10**10:010d}"
    return "0000000001" if value == "0000000000" else value


def _row(
    cik: str,
    suffix: str,
    form: str,
    filed: str,
    acceptance: str,
    primary: str,
    *,
    year: str = "20",
) -> dict[str, str]:
    return {
        "accessionNumber": f"{cik}-{year}-{suffix}",
        "filingDate": filed,
        "reportDate": "2020-01-31",
        "acceptanceDateTime": acceptance,
        "form": form,
        "primaryDocument": primary,
    }


def _index(cik: str, rows: list[dict[str, str]], files: list[dict[str, object]] | None = None) -> bytes:
    columns = {key: [row[key] for row in rows] for key in _COLUMNS}
    return json.dumps(
        {
            "cik": cik,
            "name": "Fixture Co",
            "tickers": ["ZZQX"],
            "filings": {"recent": columns, "files": [] if files is None else files},
        }
    ).encode()


def _client(tmp_path: Path, routes: dict[str, bytes]) -> tuple[SecHttpClient, _Routed]:
    routed = _Routed(routes)
    settings = SecSettings(
        sec_user_agent=_UA,
        sec_cache_dir=tmp_path,
        sec_max_retries=0,
        sec_requests_per_second=5,
    )
    client = SecHttpClient(
        settings,
        transport=httpx.MockTransport(routed),
        sleep=lambda _seconds: None,
    )
    return client, routed


def _delete_cik(session: Session, cik: str) -> None:
    session.rollback()
    company = session.scalar(select(Company).where(Company.cik == cik))
    if company is None:
        return
    session.execute(delete(Document).where(Document.company_id == company.id))
    session.execute(delete(Company).where(Company.id == company.id))
    session.commit()


def test_limit_does_not_fetch_history_or_unselected_filings(
    db_session: Session,
    tmp_path: Path,
) -> None:
    cik = _cik()
    first = _row(cik, "000001", "10-K", "2020-02-21", "2020-02-22T02:37:18.000Z", "a.htm")
    second = _row(cik, "000002", "10-Q", "2020-05-21", "2020-05-22T02:37:18.000Z", "b.htm")
    form_four = _row(cik, "000099", "4", "2020-03-01", "2020-03-02T00:00:00.000Z", "form4.xml")
    older_name = f"CIK{cik}-submissions-001.json"
    older_row = _row(
        cik,
        "000001",
        "10-K",
        "2018-02-21",
        "2018-02-22T02:00:00.000Z",
        "old.htm",
        year="18",
    )
    older = {key: [older_row[key]] for key in _COLUMNS}
    routes = {
        submissions_index_url(cik): _index(
            cik,
            [first, form_four, second],
            [
                {
                    "name": older_name,
                    "filingCount": 1,
                    "filingFrom": "2018-01-01",
                    "filingTo": "2019-12-31",
                }
            ],
        ),
        older_submissions_url(older_name, cik=cik): json.dumps(older).encode(),
        archive_document_url(cik, first["accessionNumber"], "a.htm"): b"<DOCUMENT>\n<TYPE>10-K\n",
        archive_document_url(cik, second["accessionNumber"], "b.htm"): b"<DOCUMENT>\n<TYPE>10-Q\n",
        archive_document_url(cik, older_row["accessionNumber"], "old.htm"): b"<DOCUMENT>\nold\n",
    }
    client, routed = _client(tmp_path, routes)
    first_archive = archive_document_url(cik, first["accessionNumber"], "a.htm")
    second_archive = archive_document_url(cik, second["accessionNumber"], "b.htm")
    try:
        limited = ingest_incrementally(
            db_session,
            client,
            cik=cik,
            since=_SINCE,
            ticker="ZZQX",
            limit=1,
        )
        archives = [url for url in routed.urls if "/Archives/" in url]
        assert archives == [first_archive]
        assert older_submissions_url(older_name, cik=cik) not in routed.urls
        assert limited.counts == IngestCounts(discovered=2, inserted=1, unchanged=0, failed=0)
        assert limited.discovery.older_files_skipped == (older_name,)
        finished = ingest_incrementally(
            db_session,
            client,
            cik=cik,
            since=_SINCE,
            ticker="ZZQX",
        )
    finally:
        client.close()
    assert second_archive in routed.urls
    assert older_submissions_url(older_name, cik=cik) not in routed.urls
    assert finished.counts == IngestCounts(discovered=2, inserted=1, unchanged=1, failed=0)
    assert finished.document_count == 2
    assert [row.accession_number for row in finished.stored] == [
        first["accessionNumber"],
        second["accessionNumber"],
    ]


def test_a_download_failure_is_isolated_and_retried(db_session: Session, tmp_path: Path) -> None:
    cik = _cik()
    rows = [
        _row(cik, "000001", "10-K", "2020-02-21", "2020-02-22T02:37:18.000Z", "a.htm"),
        _row(cik, "000002", "8-K", "2020-03-21", "2020-03-22T02:37:18.000Z", "b.htm"),
        _row(cik, "000003", "10-Q", "2020-05-21", "2020-05-22T02:37:18.000Z", "c.htm"),
    ]
    bodies = {
        "a.htm": b"<DOCUMENT>\n<TYPE>10-K\n",
        "b.htm": b"<DOCUMENT>\n<TYPE>8-K\n",
        "c.htm": b"<DOCUMENT>\n<TYPE>10-Q\n",
    }
    routes = {submissions_index_url(cik): _index(cik, rows)}
    for row in rows:
        if row["primaryDocument"] == "b.htm":
            continue
        routes[archive_document_url(cik, row["accessionNumber"], row["primaryDocument"])] = bodies[
            row["primaryDocument"]
        ]
    client, _routed = _client(tmp_path, routes)
    events: list[object] = []
    try:
        failed = ingest_incrementally(
            db_session,
            client,
            cik=cik,
            since=_SINCE,
            ticker="ZZQX",
            progress=events.append,
        )
        visible = PointInTimeRepository(db_session).visible_documents(
            company_id=failed.company_id,
            as_of=_AS_OF,
        )
        assert [row.accession_number for row in visible] == [
            rows[0]["accessionNumber"],
            rows[2]["accessionNumber"],
        ]
        missing = archive_document_url(cik, rows[1]["accessionNumber"], "b.htm")
        routes[missing] = bodies["b.htm"]
        retried = ingest_incrementally(
            db_session,
            client,
            cik=cik,
            since=_SINCE,
            ticker="ZZQX",
        )
    finally:
        client.close()
    assert failed.counts == IngestCounts(discovered=3, inserted=2, unchanged=0, failed=1)
    assert len(failed.failures) == 1
    failure = failed.failures[0]
    assert isinstance(failure, IngestFailure)
    assert failure.accession_number == rows[1]["accessionNumber"]
    assert failure.stage == "download"
    assert failure.error_type == "SecHttpStatusError"
    assert failure.archive_url.endswith("/b.htm")
    assert "404" in failure.message
    assert [type(event).__name__ for event in events] == [
        "StoredFiling",
        "IngestFailure",
        "StoredFiling",
    ]
    assert retried.counts == IngestCounts(discovered=3, inserted=1, unchanged=2, failed=0)
    assert retried.document_count == 3
    assert retried.failures == ()


def test_restart_leaves_row_counts_and_hashes_unchanged(
    db_session: Session,
    tmp_path: Path,
) -> None:
    cik = _cik()
    rows = [
        _row(cik, "000001", "10-K", "2020-02-21", "2020-02-22T02:37:18.000Z", "a.htm"),
        _row(cik, "000002", "10-Q", "2020-05-21", "2020-05-22T02:37:18.000Z", "b.htm"),
        _row(cik, "000003", "8-K", "2020-08-21", "2020-08-22T02:37:18.000Z", "c.htm"),
    ]
    routes = {submissions_index_url(cik): _index(cik, rows)}
    for row, body in zip(rows, (b"k", b"q", b"e"), strict=True):
        routes[archive_document_url(cik, row["accessionNumber"], row["primaryDocument"])] = body
    client, routed = _client(tmp_path, routes)
    extra: Session | None = None
    try:
        first = ingest_incrementally(
            db_session,
            client,
            cik=cik,
            since=_SINCE,
            ticker="ZZQX",
            limit=1,
            commit_each=True,
        )
        assert first.counts == IngestCounts(discovered=3, inserted=1, unchanged=0, failed=0)
        assert [url for url in routed.urls if "/Archives/" in url] == [
            archive_document_url(cik, rows[0]["accessionNumber"], "a.htm")
        ]
        extra = Session(bind=db_session.get_bind(), expire_on_commit=False)
        second = ingest_incrementally(
            extra,
            client,
            cik=cik,
            since=_SINCE,
            ticker="ZZQX",
            commit_each=True,
        )
        third = ingest_incrementally(
            extra,
            client,
            cik=cik,
            since=_SINCE,
            ticker="ZZQX",
            commit_each=True,
        )
        assert second.counts == IngestCounts(discovered=3, inserted=2, unchanged=1, failed=0)
        assert third.counts == IngestCounts(discovered=3, inserted=0, unchanged=3, failed=0)
        assert third.document_count == second.document_count == 3
        assert third.row_sha256 == second.row_sha256
        assert third.content_sha256 == second.content_sha256
        assert [row.document_id for row in third.stored] == [
            row.document_id for row in second.stored
        ]
        assert [row.sha256 for row in third.stored] == [row.sha256 for row in second.stored]
        resumed = PointInTimeRepository(extra).visible_documents(
            company_id=second.company_id,
            as_of=_AS_OF,
        )
        assert len(resumed) == 3
    finally:
        cleaner = extra if extra is not None else db_session
        _delete_cik(cleaner, cik)
        client.close()
        if extra is not None:
            extra.close()


def test_metadata_conflict_does_not_overwrite(db_session: Session, tmp_path: Path) -> None:
    cik = _cik()
    original = _row(cik, "000001", "10-K", "2020-02-21", "2020-02-22T02:37:18.000Z", "a.htm")
    conflict = _row(cik, "000001", "10-Q", "2020-02-21", "2020-02-22T02:37:18.000Z", "a.htm")
    added = _row(cik, "000002", "8-K", "2020-06-01", "2020-06-02T02:37:18.000Z", "b.htm")
    body = b"<DOCUMENT>\n<TYPE>10-K\n"
    first_client, _first_routed = _client(
        tmp_path,
        {
            submissions_index_url(cik): _index(cik, [original]),
            archive_document_url(cik, original["accessionNumber"], "a.htm"): body,
        },
    )
    second_client, _second_routed = _client(
        tmp_path / "other",
        {
            submissions_index_url(cik): _index(cik, [conflict, added]),
            archive_document_url(cik, conflict["accessionNumber"], "a.htm"): body,
            archive_document_url(cik, added["accessionNumber"], "b.htm"): b"<DOCUMENT>\n<TYPE>8-K\n",
        },
    )
    try:
        stored = ingest_incrementally(
            db_session,
            first_client,
            cik=cik,
            since=_SINCE,
            ticker="ZZQX",
        )
        before = PointInTimeRepository(db_session).visible_documents(
            company_id=stored.company_id,
            as_of=_AS_OF,
        )
        result = ingest_incrementally(
            db_session,
            second_client,
            cik=cik,
            since=_SINCE,
            ticker="ZZQX",
        )
    finally:
        first_client.close()
        second_client.close()
    assert result.counts == IngestCounts(discovered=2, inserted=1, unchanged=0, failed=1)
    assert result.failures[0].stage == "store"
    assert result.failures[0].error_type == "FilingConflictError"
    assert result.failures[0].accession_number == original["accessionNumber"]
    after = PointInTimeRepository(db_session).visible_documents(
        company_id=stored.company_id,
        as_of=_AS_OF,
    )
    kept = next(row for row in after if row.accession_number == original["accessionNumber"])
    assert kept.id == before[0].id
    assert kept.form_type == "10-K"
    assert kept.known_at == before[0].known_at
    assert [row.form_type for row in after] == ["10-K", "8-K"]


def test_runner_rejects_an_unbounded_or_invalid_window(db_session: Session, tmp_path: Path) -> None:
    client, routed = _client(tmp_path, {})
    try:
        with pytest.raises(ValueError, match="limit"):
            ingest_incrementally(
                db_session,
                client,
                cik="0001045810",
                since=_SINCE,
                limit=0,
            )
        with pytest.raises(ValueError, match="limit"):
            ingest_incrementally(
                db_session,
                client,
                cik="0001045810",
                since=_SINCE,
                limit=True,  # type: ignore[arg-type]
            )
        with pytest.raises(TypeError, match="since"):
            ingest_incrementally(
                db_session,
                client,
                cik="0001045810",
                since=datetime(2020, 1, 1, tzinfo=UTC),
            )
        with pytest.raises(SecSubmissionError, match="ticker"):
            ingest_incrementally(
                db_session,
                client,
                cik="0001045810",
                since=_SINCE,
                ticker=" ",
            )
    finally:
        client.close()
    assert routed.urls == []
