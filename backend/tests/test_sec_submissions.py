"""Submissions discovery and primary-document download, using recorded fixtures."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from titan.common.settings import SecSettings
from titan.ingest.acceptance import parse_acceptance_datetime
from titan.ingest.errors import (
    FilingConflictError,
    SecAcceptanceError,
    SecHttpStatusError,
    SecIdentityError,
    SecSubmissionError,
)
from titan.ingest.identifiers import (
    archive_document_url,
    normalize_cik,
    older_submissions_url,
    submissions_index_url,
    validate_accession,
    validate_primary_document,
    validate_submission_filename,
)
from titan.ingest.sec_transport import (
    EndpointFreshness,
    SecHttpClient,
    classify_endpoint,
)
from titan.ingest.submissions import (
    TARGET_FORMS,
    discover_filings,
    download_primary_documents,
    parse_since,
)

_UA = "Titan Research titan@example.com"
_FIXTURES = Path(__file__).parent / "fixtures" / "sec"
_CIK = "0001045810"
_SINCE = date(2019, 1, 1)
_COLUMNS = (
    "accessionNumber",
    "filingDate",
    "reportDate",
    "acceptanceDateTime",
    "form",
    "primaryDocument",
)
_BODIES = {
    "nvda-20240128.htm": b"<DOCUMENT>\n<TYPE>10-K\n<FILENAME>nvda-20240128.htm\n",
    "nvda-20240128a.htm": b"<DOCUMENT>\n<TYPE>10-K/A\n",
    "d48176d8k.htm": b"<DOCUMENT>\n<TYPE>8-K\n",
    "nvda-2019x10k.htm": b"<DOCUMENT>\r\n<TYPE>10-K\r\n<FILENAME>nvda-2019x10k.htm\r\n\xff",
    "form8-kq1fy20.htm": b"<DOCUMENT>\n<TYPE>8-K/A\n",
}


class Routed:
    """Maps a canonical SEC URL to response bytes."""

    def __init__(self, routes: dict[str, bytes]) -> None:
        self.routes = routes
        self.urls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.urls.append(url)
        body = self.routes.get(url)
        if body is None:
            return httpx.Response(404, request=request)
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "text/html"},
            request=request,
        )


def _fixture_routes() -> dict[str, bytes]:
    routes = {submissions_index_url(_CIK): (_FIXTURES / "CIK0001045810.json").read_bytes()}
    for name in (
        "CIK0001045810-submissions-001.json",
        "CIK0001045810-submissions-002.json",
        "CIK0001045810-submissions-003.json",
    ):
        routes[older_submissions_url(name, cik=_CIK)] = (_FIXTURES / name).read_bytes()
    return routes


@contextmanager
def _client(tmp_path: Path, routes: dict[str, bytes]) -> Iterator[tuple[SecHttpClient, Routed]]:
    routed = Routed(routes)
    settings = SecSettings(sec_user_agent=_UA, sec_cache_dir=tmp_path)
    client = SecHttpClient(settings, transport=httpx.MockTransport(routed))
    try:
        yield client, routed
    finally:
        client.close()


def _index(
    rows: list[dict[str, str]],
    files: list[dict[str, object]] | None = None,
    *,
    cik: str = _CIK,
) -> bytes:
    columns = {key: [row[key] for row in rows] for key in _COLUMNS}
    payload = {
        "cik": cik,
        "name": "NVIDIA CORP",
        "tickers": ["NVDA"],
        "filings": {"recent": columns, "files": [] if files is None else files},
    }
    return json.dumps(payload).encode()


def _row(
    accession: str,
    form: str,
    filed: str,
    acceptance: str,
    primary: str,
    report: str = "",
) -> dict[str, str]:
    return {
        "accessionNumber": accession,
        "filingDate": filed,
        "reportDate": report,
        "acceptanceDateTime": acceptance,
        "form": form,
        "primaryDocument": primary,
    }


def test_acceptance_time_matches_the_edgar_header() -> None:
    samples = (
        (
            "2019-02-22T02:37:18.000Z",
            datetime(2019, 2, 21, 21, 37, 18, tzinfo=UTC),
            datetime(2019, 2, 21, 16, 37, 18),
            timedelta(hours=-5),
        ),
        (
            "2024-02-22T02:36:57.000Z",
            datetime(2024, 2, 21, 21, 36, 57, tzinfo=UTC),
            datetime(2024, 2, 21, 16, 36, 57),
            timedelta(hours=-5),
        ),
        (
            "2026-09-03T16:03:56.000Z",
            datetime(2026, 9, 3, 12, 3, 56, tzinfo=UTC),
            datetime(2026, 9, 3, 8, 3, 56),
            timedelta(hours=-4),
        ),
        (
            "2024-03-15T00:30:50.000Z",
            datetime(2024, 3, 14, 20, 30, 50, tzinfo=UTC),
            datetime(2024, 3, 14, 16, 30, 50),
            timedelta(hours=-4),
        ),
        (
            "2024-11-08T02:35:26.000Z",
            datetime(2024, 11, 7, 21, 35, 26, tzinfo=UTC),
            datetime(2024, 11, 7, 16, 35, 26),
            timedelta(hours=-5),
        ),
    )
    for original, utc, eastern_wall, offset in samples:
        parsed = parse_acceptance_datetime(original)
        assert parsed.original == original
        assert parsed.precision == 3
        assert parsed.utc == utc
        published = datetime.fromisoformat(original)
        assert parsed.utc != published
        assert parsed.eastern.utcoffset() == offset
        assert parsed.eastern.replace(tzinfo=None) == eastern_wall
        assert parsed.eastern.astimezone(UTC) == parsed.utc


def test_acceptance_precision_keeps_trailing_zeros() -> None:
    parsed = parse_acceptance_datetime("2019-02-22T02:37:18.123Z")
    assert parsed.precision == 3
    assert parsed.utc.microsecond == 123_000
    assert parsed.original == "2019-02-22T02:37:18.123Z"
    whole_seconds = parse_acceptance_datetime("2019-02-22T02:37:18Z")
    assert whole_seconds.precision == 0
    assert whole_seconds.utc == parsed.utc.replace(microsecond=0)
    tenth = parse_acceptance_datetime("2019-02-22T02:37:18.1Z")
    assert tenth.precision == 1
    assert tenth.utc.microsecond == 100_000


@pytest.mark.parametrize(
    "value",
    [
        "",
        "2019-02-22T02:37:18",
        "2019-02-22T02:37:18.000+00:00",
        "2019-02-22T02:37:18.000-05:00",
        "2019-02-22 02:37:18.000Z",
        "2019-02-22T02:37:18.0000000Z",
        "2019-02-31T02:37:18.000Z",
        "2019-02-22T02:37:18.000z",
    ],
)
def test_acceptance_rejects_values_that_would_be_guessed(value: str) -> None:
    with pytest.raises(SecAcceptanceError):
        parse_acceptance_datetime(value)


def test_since_accepts_a_year_or_a_date() -> None:
    assert parse_since("2019") == date(2019, 1, 1)
    assert parse_since("2019-02-21") == date(2019, 2, 21)
    with pytest.raises(ValueError):
        parse_since("2019-02-31")
    with pytest.raises(ValueError):
        parse_since("19")
    with pytest.raises(ValueError):
        parse_since("2019-1-1")
    with pytest.raises(ValueError):
        parse_since("0000")


def test_cik_accession_and_archive_url() -> None:
    assert normalize_cik("1045810") == _CIK
    assert normalize_cik(_CIK) == _CIK
    assert validate_accession("0001193125-26-275783") == "0001193125-26-275783"
    url = archive_document_url(_CIK, "0001193125-26-275783", "d48176d8k.htm")
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000119312526275783/d48176d8k.htm"
    )
    assert "-" not in url.split("/Archives/edgar/data/1045810/", 1)[1].split("/", 1)[0]
    assert classify_endpoint(url) is EndpointFreshness.IMMUTABLE
    assert classify_endpoint(submissions_index_url(_CIK)) is EndpointFreshness.MUTABLE
    nested = archive_document_url(
        "0000000007",
        "0000000007-24-000001",
        "xslF345X03/wf-form4_160037992742467.xml",
    )
    assert nested == (
        "https://www.sec.gov/Archives/edgar/data/7/"
        "000000000724000001/xslF345X03/wf-form4_160037992742467.xml"
    )
    for bad in (
        "",
        "12345678901",
        "0000000000",
        "CIK0001045810",
        "../1045810",
        "1045810 ",
        "10 45810",
    ):
        with pytest.raises(SecIdentityError):
            normalize_cik(bad)
    with pytest.raises(SecIdentityError):
        validate_accession("000104581019000023")
    with pytest.raises(SecIdentityError):
        validate_submission_filename("../CIK0001045810-submissions-001.json", cik=_CIK)
    with pytest.raises(SecIdentityError):
        validate_submission_filename("CIK0000320193-submissions-001.json", cik=_CIK)


@pytest.mark.parametrize(
    "primary",
    [
        "../secret.htm",
        "..",
        ".",
        "/etc/passwd",
        "foo/../../secret.htm",
        "foo/../secret.htm",
        "%2e%2e/secret.htm",
        "%2e%2e%2fsecret.htm",
        "..%2fsecret.htm",
        "foo\\secret.htm",
        "foo/./secret.htm",
        "//secret.htm",
        "secret.htm/..",
        "",
        "secret.htm\x00",
        "https://evil.example/secret.htm",
        "secret.htm?x=1",
        "secret.htm#x",
        " secret.htm",
        "xslF345X03/../secret.htm",
    ],
)
def test_primary_document_rejects_path_escape(primary: str) -> None:
    with pytest.raises(SecIdentityError):
        validate_primary_document(primary)
    with pytest.raises(SecIdentityError):
        archive_document_url(_CIK, "0001045810-19-000023", primary)


@pytest.mark.parametrize(
    "primary",
    [
        "nvda-2019x10k.htm",
        "d48176d8k.htm",
        "form8-kq1fy20.htm",
        "a8-k2020annualmeetingr.htm",
        "xslF345X03/wf-form4_160037992742467.xml",
    ],
)
def test_primary_document_accepts_a_filing_filename(primary: str) -> None:
    assert validate_primary_document(primary) == primary


def test_since_2019_reads_recent_and_overlapping_older_files(tmp_path: Path) -> None:
    with _client(tmp_path, _fixture_routes()) as (client, routed):
        discovery = discover_filings(client, "1045810", since=parse_since("2019"))
        assert routed.urls == [
            submissions_index_url(_CIK),
            older_submissions_url("CIK0001045810-submissions-001.json", cik=_CIK),
            older_submissions_url("CIK0001045810-submissions-003.json", cik=_CIK),
        ]
        cached = client.cache.load(submissions_index_url(_CIK))
        assert cached is not None
        assert cached.body == (_FIXTURES / "CIK0001045810.json").read_bytes()
        again = len(routed.urls)
        assert discover_filings(client, _CIK, since=_SINCE).filings == discovery.filings
        assert len(routed.urls) == again

    assert discovery.cik == _CIK
    assert discovery.company_name == "NVIDIA CORP"
    assert discovery.tickers == ("NVDA",)
    assert discovery.recent_oldest_filing_date == date(2024, 2, 21)
    assert discovery.recent_oldest_filing_date > _SINCE
    assert discovery.older_files_fetched == (
        "CIK0001045810-submissions-001.json",
        "CIK0001045810-submissions-003.json",
    )
    assert discovery.older_files_skipped == ("CIK0001045810-submissions-002.json",)
    selected = [
        (item.form_type, item.accession_number, item.source_name) for item in discovery.filings
    ]
    assert selected == [
        ("10-K", "0001045810-24-000029", "recent"),
        ("10-K/A", "0001045810-24-000099", "recent"),
        ("8-K", "0001193125-24-000111", "recent"),
        ("10-K", "0001045810-19-000023", "CIK0001045810-submissions-001.json"),
        ("8-K/A", "0001045810-19-000088", "CIK0001045810-submissions-001.json"),
    ]
    assert {item.form_type for item in discovery.filings} <= TARGET_FORMS
    original = discovery.filings[3]
    assert original.filing_date == date(2019, 2, 21)
    assert original.period_of_report == date(2019, 1, 27)
    assert original.acceptance.original == "2019-02-22T02:37:18.000Z"
    assert original.acceptance.precision == 3
    assert original.acceptance.utc == datetime(2019, 2, 21, 21, 37, 18, tzinfo=UTC)
    assert original.primary_document == "nvda-2019x10k.htm"
    assert original.archive_url == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581019000023/nvda-2019x10k.htm"
    )
    third_party = discovery.filings[2]
    assert third_party.archive_url == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000119312524000111/d48176d8k.htm"
    )


def test_primary_documents_keep_original_bytes_and_hashes(tmp_path: Path) -> None:
    with _client(tmp_path, _fixture_routes()) as (client, routed):
        discovery = discover_filings(client, _CIK, since=_SINCE)
        for filing in discovery.filings:
            routed.routes[filing.archive_url] = _BODIES[filing.primary_document]
        before_download = len(routed.urls)
        first = download_primary_documents(client, discovery.filings)
        second = download_primary_documents(client, discovery.filings)

    assert [item.filing.primary_document for item in first] == [
        "nvda-20240128.htm",
        "nvda-20240128a.htm",
        "d48176d8k.htm",
        "nvda-2019x10k.htm",
        "form8-kq1fy20.htm",
    ]
    downloaded_urls = routed.urls[before_download:]
    assert downloaded_urls == [item.filing.archive_url for item in first]
    assert all("/wf-form4_" not in url for url in routed.urls)
    assert all("nvda-2018x10k" not in url and "nvda-2017x10k" not in url for url in routed.urls)
    assert all("nvda-2012q1" not in url for url in routed.urls)
    original = first[3]
    assert original.body == _BODIES["nvda-2019x10k.htm"]
    assert original.sha256 == hashlib.sha256(original.body).hexdigest()
    assert original.byte_count == len(original.body)
    assert b"\r\n" in original.body
    assert original.body.endswith(b"\xff")
    assert second[3].from_cache is True
    assert second[3].body == original.body
    assert second[3].sha256 == original.sha256
    with _client(tmp_path, {}) as (client, _routed):
        cached = client.cache.load(original.filing.archive_url)
    assert cached is not None
    assert cached.body == original.body
    assert cached.sha256 == original.sha256


def test_missing_older_file_is_not_treated_as_an_empty_history(tmp_path: Path) -> None:
    routes = _fixture_routes()
    del routes[older_submissions_url("CIK0001045810-submissions-001.json", cik=_CIK)]
    with _client(tmp_path, routes) as (client, _routed), pytest.raises(SecHttpStatusError):
        discover_filings(client, _CIK, since=_SINCE)


def test_unequal_columns_and_a_short_file_fail(tmp_path: Path) -> None:
    routes = _fixture_routes()
    index = json.loads(routes[submissions_index_url(_CIK)])
    index["filings"]["recent"]["accessionNumber"].pop()
    routes[submissions_index_url(_CIK)] = json.dumps(index).encode()
    with (
        _client(tmp_path / "columns", routes) as (client, routed),
        pytest.raises(SecSubmissionError, match="differ in length"),
    ):
        discover_filings(client, _CIK, since=_SINCE)
    assert all("/Archives/" not in url for url in routed.urls)

    routes = _fixture_routes()
    index = json.loads(routes[submissions_index_url(_CIK)])
    index["filings"]["files"][2]["filingCount"] = 9
    routes[submissions_index_url(_CIK)] = json.dumps(index).encode()
    with (
        _client(tmp_path / "count", routes) as (client, routed),
        pytest.raises(SecSubmissionError, match="declares 9"),
    ):
        discover_filings(client, _CIK, since=_SINCE)
    assert older_submissions_url("CIK0001045810-submissions-003.json", cik=_CIK) in routed.urls


def test_bad_filing_bounds_are_not_skipped(tmp_path: Path) -> None:
    routes = _fixture_routes()
    index = json.loads(routes[submissions_index_url(_CIK)])
    index["filings"]["files"][1]["filingTo"] = "2018/12/31"
    routes[submissions_index_url(_CIK)] = json.dumps(index).encode()
    with (
        _client(tmp_path, routes) as (client, _routed),
        pytest.raises(SecSubmissionError, match="filingTo"),
    ):
        discover_filings(client, _CIK, since=_SINCE)


def test_cik_and_primary_document_are_rejected_before_download(tmp_path: Path) -> None:
    with _client(tmp_path, _fixture_routes()) as (client, routed):
        with pytest.raises(SecIdentityError):
            discover_filings(client, "../0001045810", since=_SINCE)
        assert routed.urls == []
        routes = _fixture_routes()
        index = json.loads(routes[submissions_index_url(_CIK)])
        index["cik"] = "0000320193"
        routes[submissions_index_url(_CIK)] = json.dumps(index).encode()
        with (
            _client(tmp_path / "cik", routes) as (client, routed),
            pytest.raises(SecSubmissionError, match="CIK"),
        ):
            discover_filings(client, _CIK, since=_SINCE)
    assert all("/Archives/" not in url for url in routed.urls)

    index = json.loads(_fixture_routes()[submissions_index_url(_CIK)])
    index["filings"]["recent"]["primaryDocument"][0] = "../secret.htm"
    routes = {submissions_index_url(_CIK): json.dumps(index).encode()}
    with (
        _client(tmp_path / "escape", routes) as (client, routed),
        pytest.raises(SecIdentityError),
    ):
        discover_filings(client, _CIK, since=_SINCE)
    assert all("/Archives/" not in url for url in routed.urls)


def test_ignored_forms_do_not_block_or_get_downloaded(tmp_path: Path) -> None:
    rows = [
        _row(
            "0001045810-24-000029",
            "10-K",
            "2024-02-21",
            "2024-02-22T02:36:57.000Z",
            "nvda-20240128.htm",
            "2024-01-28",
        ),
        _row(
            "0001045810-24-000050",
            "4",
            "2024-05-01",
            "2024-05-02T00:00:00.000Z",
            "../evil.htm",
        ),
    ]
    routes = {submissions_index_url(_CIK): _index(rows)}
    with _client(tmp_path, routes) as (client, routed):
        discovery = discover_filings(client, _CIK, since=_SINCE)
        routed.routes[discovery.filings[0].archive_url] = _BODIES["nvda-20240128.htm"]
        downloaded = download_primary_documents(client, discovery.filings)
    assert [item.form_type for item in discovery.filings] == ["10-K"]
    assert downloaded[0].body == _BODIES["nvda-20240128.htm"]
    assert all("evil" not in url for url in routed.urls)


def test_rows_before_since_do_not_need_a_primary_document(tmp_path: Path) -> None:
    rows = [
        _row(
            "0001045810-24-000029",
            "10-K",
            "2024-02-21",
            "2024-02-22T02:36:57.000Z",
            "nvda-20240128.htm",
            "2024-01-28",
        ),
        _row("0001012870-99-004592", "10-Q", "1999-12-10", "n/a", "", "1999-10-31"),
    ]
    routes = {submissions_index_url(_CIK): _index(rows)}
    with _client(tmp_path, routes) as (client, _routed):
        discovery = discover_filings(client, _CIK, since=_SINCE)
        assert [item.accession_number for item in discovery.filings] == ["0001045810-24-000029"]
        with pytest.raises(SecIdentityError):
            discover_filings(client, _CIK, since=None)


def test_empty_recent_still_reads_the_older_file(tmp_path: Path) -> None:
    older_name = "CIK0001045810-submissions-001.json"
    older = {
        key: [value]
        for key, value in _row(
            "0001045810-19-000023",
            "10-K",
            "2019-02-21",
            "2019-02-22T02:37:18.000Z",
            "nvda-2019x10k.htm",
            "2019-01-27",
        ).items()
    }
    files = [
        {
            "name": older_name,
            "filingCount": 1,
            "filingFrom": "2019-01-01",
            "filingTo": "2019-12-31",
        }
    ]
    routes = {
        submissions_index_url(_CIK): _index([], files),
        older_submissions_url(older_name, cik=_CIK): json.dumps(older).encode(),
    }
    with _client(tmp_path, routes) as (client, routed):
        discovery = discover_filings(client, _CIK, since=_SINCE)
    assert discovery.recent_filing_count == 0
    assert discovery.older_files_fetched == (older_name,)
    assert [item.accession_number for item in discovery.filings] == ["0001045810-19-000023"]
    assert older_submissions_url(older_name, cik=_CIK) in routed.urls


def test_conflicting_duplicate_accession_is_rejected(tmp_path: Path) -> None:
    older_name = "CIK0001045810-submissions-001.json"
    recent = [
        _row(
            "0001045810-24-000029",
            "10-K",
            "2024-02-21",
            "2024-02-22T02:36:57.000Z",
            "nvda-20240128.htm",
            "2024-01-28",
        )
    ]
    older_row = _row(
        "0001045810-24-000029",
        "10-K",
        "2024-02-21",
        "2024-02-22T02:36:57.000Z",
        "other.htm",
        "2024-01-28",
    )
    older = {key: [older_row[key]] for key in _COLUMNS}
    routes = {
        submissions_index_url(_CIK): _index(
            recent,
            [{"name": older_name, "filingCount": 1, "filingTo": "2024-02-21"}],
        ),
        older_submissions_url(older_name, cik=_CIK): json.dumps(older).encode(),
    }
    with _client(tmp_path, routes) as (client, routed), pytest.raises(FilingConflictError):
        discover_filings(client, _CIK, since=_SINCE)
    assert all("/Archives/" not in url for url in routed.urls)


def test_spaced_form_code_is_not_silently_dropped(tmp_path: Path) -> None:
    rows = [
        _row(
            "0001045810-24-000029",
            "10-K ",
            "2024-02-21",
            "2024-02-22T02:36:57.000Z",
            "nvda-20240128.htm",
            "2024-01-28",
        )
    ]
    routes = {submissions_index_url(_CIK): _index(rows)}
    with (
        _client(tmp_path, routes) as (client, _routed),
        pytest.raises(SecSubmissionError, match="form type"),
    ):
        discover_filings(client, _CIK, since=_SINCE)
