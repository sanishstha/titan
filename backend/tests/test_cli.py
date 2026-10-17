"""``titan ingest --ticker NVDA --since 2020`` resolves a CIK and stores filings."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from titan.cli import app, run_ingest
from titan.common.session import create_db_engine, create_session_factory
from titan.common.settings import SecSettings
from titan.ingest.errors import SecIdentityError
from titan.ingest.identifiers import archive_document_url, submissions_index_url
from titan.ingest.run import IncrementalIngestResult, IngestCounts, IngestFailure
from titan.ingest.sec_transport import SecHttpClient
from titan.ingest.submissions import SubmissionDiscovery
from titan.ingest.tickers import company_tickers_url, match_ticker, normalize_ticker
from titan.models import Company
from titan.pit.repository import PointInTimeRepository

_UA = "Titan Research titan@example.com"
_RUNNER = CliRunner()


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


def _empty_result() -> IncrementalIngestResult:
    discovery = SubmissionDiscovery(
        cik="0001045810",
        company_name="NVIDIA CORP",
        tickers=("NVDA",),
        filings=(),
        older_files_fetched=(),
        older_files_skipped=(),
        recent_filing_count=0,
        recent_oldest_filing_date=None,
    )
    return IncrementalIngestResult(
        company_id=UUID(int=1),
        discovery=discovery,
        stored=(),
        failures=(),
        counts=IngestCounts(discovered=0, inserted=0, unchanged=0, failed=0),
        document_count=0,
        row_sha256="a" * 64,
        content_sha256="b" * 64,
    )


def test_ingest_command_accepts_ticker_and_since(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake(
        ticker: str,
        since: date,
        *,
        limit: int | None = None,
        progress: object = None,
    ) -> IncrementalIngestResult:
        del progress
        seen["ticker"] = ticker
        seen["since"] = since
        seen["limit"] = limit
        return _empty_result()

    monkeypatch.setattr("titan.cli.run_ingest", fake)
    result = _RUNNER.invoke(app, ["ingest", "--ticker", "NVDA", "--since", "2020"])
    assert result.exit_code == 0
    assert seen == {"ticker": "NVDA", "since": date(2020, 1, 1), "limit": None}
    assert "0001045810 NVIDIA CORP" in result.stdout
    assert "discovered 0 inserted 0 unchanged 0 failed 0" in result.stdout
    assert "documents 0" in result.stdout
    assert "row_sha256 " + "a" * 64 in result.stdout
    assert "content_sha256 " + "b" * 64 in result.stdout


def test_ingest_command_passes_a_download_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake(
        ticker: str,
        since: date,
        *,
        limit: int | None = None,
        progress: object = None,
    ) -> IncrementalIngestResult:
        del ticker, since, progress
        seen["limit"] = limit
        return _empty_result()

    monkeypatch.setattr("titan.cli.run_ingest", fake)
    result = _RUNNER.invoke(
        app,
        ["ingest", "--ticker", "NVDA", "--since", "2020", "--limit", "2"],
    )
    assert result.exit_code == 0
    assert seen == {"limit": 2}


def test_ingest_command_rejects_a_nonpositive_limit() -> None:
    result = _RUNNER.invoke(
        app,
        ["ingest", "--ticker", "NVDA", "--since", "2020", "--limit", "0"],
    )
    assert result.exit_code != 0


def test_ingest_command_reports_structured_filing_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = IngestFailure(
        accession_number="0001045810-20-000010",
        archive_url="https://www.sec.gov/Archives/edgar/data/1045810/example.htm",
        stage="download",
        error_type="SecHttpStatusError",
        message="SEC returned HTTP 404 for https://www.sec.gov/example",
    )
    result_row = _empty_result()
    failed = IncrementalIngestResult(
        company_id=result_row.company_id,
        discovery=result_row.discovery,
        stored=(),
        failures=(failure,),
        counts=IngestCounts(discovered=1, inserted=0, unchanged=0, failed=1),
        document_count=0,
        row_sha256=result_row.row_sha256,
        content_sha256=result_row.content_sha256,
    )

    def fake(
        ticker: str,
        since: date,
        *,
        limit: int | None = None,
        progress: object = None,
    ) -> IncrementalIngestResult:
        del ticker, since, limit, progress
        return failed

    monkeypatch.setattr("titan.cli.run_ingest", fake)
    result = _RUNNER.invoke(app, ["ingest", "--ticker", "NVDA", "--since", "2020"])
    assert result.exit_code == 1
    assert "discovered 1 inserted 0 unchanged 0 failed 1" in result.stdout
    assert "failed 0001045810-20-000010 download SecHttpStatusError:" in result.output


def test_ingest_command_reports_an_unknown_ticker(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(
        ticker: str,
        since: date,
        *,
        limit: int | None = None,
        progress: object = None,
    ) -> IncrementalIngestResult:
        del since, limit, progress
        raise SecIdentityError(f"ticker {ticker} is not in the SEC company ticker list")

    monkeypatch.setattr("titan.cli.run_ingest", fake)
    result = _RUNNER.invoke(app, ["ingest", "--ticker", "ZZQX", "--since", "2020"])
    assert result.exit_code == 1
    assert "ZZQX" in result.output


def test_ingest_command_rejects_a_bad_since() -> None:
    result = _RUNNER.invoke(app, ["ingest", "--ticker", "NVDA", "--since", "2020-13-01"])
    assert result.exit_code == 1
    assert "since" in result.output


def test_ingest_help_shows_the_flags() -> None:
    result = _RUNNER.invoke(app, ["ingest", "--help"])
    assert result.exit_code == 0
    assert "--ticker" in result.stdout
    assert "--since" in result.stdout
    assert "--limit" in result.stdout


def test_ticker_list_resolves_one_cik() -> None:
    payload = json.dumps(
        {
            "0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
            "1": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        }
    ).encode()
    assert normalize_ticker(" nvda ") == "NVDA"
    match = match_ticker(payload, "nvda")
    assert match.ticker == "NVDA"
    assert match.cik == "0001045810"
    with pytest.raises(SecIdentityError, match="not in the SEC"):
        match_ticker(payload, "ZZQX")
    with pytest.raises(SecIdentityError):
        normalize_ticker("../NVDA")
    duplicated = json.dumps(
        {
            "0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
            "1": {"cik_str": 320193, "ticker": "NVDA", "title": "Other"},
        }
    ).encode()
    with pytest.raises(SecIdentityError, match="more than one CIK"):
        match_ticker(duplicated, "NVDA")


def test_run_ingest_stores_the_company_for_a_ticker(db_session: Session, tmp_path: Path) -> None:
    cik = _cik()
    accession = f"{cik}-20-000010"
    primary = "annual.htm"
    tickers = json.dumps(
        {"0": {"cik_str": int(cik), "ticker": "ZZQX", "title": "Fixture Co"}}
    ).encode()
    submissions = json.dumps(
        {
            "cik": cik,
            "name": "Fixture Co",
            "tickers": ["ZZQX"],
            "filings": {
                "recent": {
                    "accessionNumber": [accession],
                    "filingDate": ["2020-02-21"],
                    "reportDate": ["2020-01-31"],
                    "acceptanceDateTime": ["2020-02-22T02:37:18.000Z"],
                    "form": ["10-K"],
                    "primaryDocument": [primary],
                },
                "files": [],
            },
        }
    ).encode()
    archive = archive_document_url(cik, accession, primary)
    routes = {
        company_tickers_url(): tickers,
        submissions_index_url(cik): submissions,
        archive: b"<DOCUMENT>\n<TYPE>10-K\n",
    }
    settings = SecSettings(sec_user_agent=_UA, sec_cache_dir=tmp_path)
    client = SecHttpClient(settings, transport=httpx.MockTransport(_Routed(routes)))
    try:
        first = run_ingest("zzqx", date(2020, 1, 1), client=client, session=db_session)
        second = run_ingest("ZZQX", date(2020, 1, 1), client=client, session=db_session)
    finally:
        client.close()
    assert first.discovery.cik == cik
    assert first.counts == IngestCounts(discovered=1, inserted=1, unchanged=0, failed=0)
    assert second.counts == IngestCounts(discovered=1, inserted=0, unchanged=1, failed=0)
    assert first.document_count == 1
    assert second.document_count == 1
    assert second.row_sha256 == first.row_sha256
    assert second.content_sha256 == first.content_sha256
    assert [row.inserted for row in first.stored] == [True]
    assert [row.inserted for row in second.stored] == [False]
    company = db_session.get(Company, first.company_id)
    assert company is not None
    assert company.cik == cik
    assert company.ticker == "ZZQX"
    visible = PointInTimeRepository(db_session).visible_documents(
        company_id=first.company_id,
        as_of=datetime(9999, 1, 1, tzinfo=UTC),
    )
    assert [row.accession_number for row in visible] == [accession]
    assert visible[0].known_at == datetime(2020, 2, 21, 21, 37, 18, tzinfo=UTC)
