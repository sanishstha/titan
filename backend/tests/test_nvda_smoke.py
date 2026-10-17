"""Opt-in live check against SEC submissions for NVIDIA.

Run with ``TITAN_SEC_SMOKE=1``. The test uses ``SEC_USER_AGENT``. It checks that
``--since 2019`` still finds the 2019 10-K when that filing is outside the
recent window, and that the primary document bytes and hash are the archive
response. It does not parse sections.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from titan.common.session import create_db_engine, create_session_factory
from titan.common.settings import get_sec_settings
from titan.ingest.sec_transport import SecHttpClient
from titan.ingest.store import ensure_company, store_primary_documents
from titan.ingest.submissions import (
    PrimaryDocument,
    discover_filings,
    download_primary_documents,
    parse_since,
)
from titan.pit.repository import PointInTimeRepository

_CIK = "0001045810"
_ACCESSION = "0001045810-19-000023"
_PREFIX = b"<DOCUMENT>\n<TYPE>10-K\n<SEQUENCE>1\n<FILENAME>nvda-2019x10k.htm\n"

pytestmark = pytest.mark.skipif(
    os.environ.get("TITAN_SEC_SMOKE") != "1",
    reason="set TITAN_SEC_SMOKE=1 to fetch live NVDA submissions",
)


def test_live_nvda_since_2019_includes_the_older_10k(tmp_path: Path) -> None:
    settings = get_sec_settings().model_copy(update={"sec_cache_dir": tmp_path})
    with SecHttpClient(settings) as client:
        discovery = discover_filings(client, _CIK, since=parse_since("2019"))
        filing = next(item for item in discovery.filings if item.accession_number == _ACCESSION)
        if (
            discovery.recent_oldest_filing_date is None
            or discovery.recent_oldest_filing_date > filing.filing_date
        ):
            assert filing.source_name != "recent"
            assert filing.source_name in discovery.older_files_fetched
        forms = {item.form_type for item in discovery.filings}
        assert {"10-K", "10-Q", "8-K"} <= forms
        assert any(form.endswith("/A") for form in forms)
        assert all(item.filing_date >= date(2019, 1, 1) for item in discovery.filings)
        assert filing.form_type == "10-K"
        assert filing.filing_date == date(2019, 2, 21)
        assert filing.period_of_report == date(2019, 1, 27)
        assert filing.primary_document == "nvda-2019x10k.htm"
        assert filing.acceptance.original == "2019-02-22T02:37:18.000Z"
        assert filing.acceptance.precision == 3
        assert filing.acceptance.utc == datetime(2019, 2, 21, 21, 37, 18, tzinfo=UTC)
        assert filing.acceptance.eastern.replace(tzinfo=None) == datetime(2019, 2, 21, 16, 37, 18)
        first = download_primary_documents(client, (filing,))
        second = download_primary_documents(client, (filing,))
        document = first[0]
        assert document.body.startswith(_PREFIX)
        assert document.sha256 == hashlib.sha256(document.body).hexdigest()
        assert document.byte_count == len(document.body)
        assert document.byte_count > 1_000_000
        assert second[0].from_cache is True
        assert second[0].body == document.body
        cached = client.cache.load(document.filing.archive_url)
        assert cached is not None
        assert cached.body == document.body
        assert cached.sha256 == document.sha256
        _store_when_postgres_is_up(document)


def _store_when_postgres_is_up(document: PrimaryDocument) -> None:
    engine = create_db_engine()
    try:
        with engine.connect() as connection:
            found = connection.execute(text("SELECT to_regclass('public.documents')")).scalar_one()
    except SQLAlchemyError:
        engine.dispose()
        return
    if found is None:
        engine.dispose()
        return
    session = create_session_factory(engine)()
    try:
        company_id = ensure_company(
            session,
            cik=_CIK,
            name="NVIDIA CORP",
            ticker="NVDA",
        )
        before = _visible_ids(session, company_id)
        first = store_primary_documents(session, company_id, (document,))
        second = store_primary_documents(session, company_id, (document,))
        after = _visible_ids(session, company_id)
        assert len(after) == 1
        assert second[0].inserted is False
        assert second[0].document_id == first[0].document_id
        assert first[0].known_at == datetime(2019, 2, 21, 21, 37, 18, tzinfo=UTC)
        assert first[0].sha256 == document.sha256
        assert first[0].inserted is (len(before) == 0)
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def _visible_ids(session: Session, company_id: UUID) -> tuple[object, ...]:
    rows = PointInTimeRepository(session).visible_documents(
        company_id=company_id,
        as_of=datetime(9999, 1, 1, tzinfo=UTC),
    )
    return tuple(row for row in rows if row.accession_number == _ACCESSION)
