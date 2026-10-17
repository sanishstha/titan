"""Typed SEC submissions discovery and primary-document download.

Company submissions are columnar. ``filings.recent`` holds the newest rows.
Older rows live in separate JSON files named by ``filings.files``. A ``since``
cutoff reads every referenced file whose ``filingTo`` is missing or is on or
after that date. A file that ends before the cutoff is recorded as skipped.
A referenced file that cannot be fetched is an error, so a 2019 filing hiding
past the recent window is not dropped.

Only 10-K, 10-Q, 8-K, and their ``/A`` amendments are returned. The original
primary-document bytes are downloaded and hashed. They are not parsed into
sections.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from titan.ingest.acceptance import AcceptanceTimestamp, parse_acceptance_datetime
from titan.ingest.errors import (
    CacheIntegrityError,
    FilingConflictError,
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
from titan.ingest.sec_transport import SecHttpClient

TARGET_FORMS = frozenset({"10-K", "10-Q", "8-K", "10-K/A", "10-Q/A", "8-K/A"})
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_SINCE_YEAR = re.compile(r"^[0-9]{4}$")
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class FilingColumns(BaseModel):
    """One SEC columnar filing list. Unknown keys are ignored."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    accession_number: list[StrictStr] = Field(alias="accessionNumber")
    filing_date: list[StrictStr] = Field(alias="filingDate")
    report_date: list[StrictStr] = Field(alias="reportDate")
    acceptance_date_time: list[StrictStr] = Field(alias="acceptanceDateTime")
    form: list[StrictStr]
    primary_document: list[StrictStr] = Field(alias="primaryDocument")


class SubmissionFileRef(BaseModel):
    """A pointer at an older submissions JSON file."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: StrictStr
    filing_count: StrictInt | None = Field(default=None, alias="filingCount", ge=0)
    filing_from: StrictStr | None = Field(default=None, alias="filingFrom")
    filing_to: StrictStr | None = Field(default=None, alias="filingTo")


class SubmissionFilings(BaseModel):
    """The ``filings`` object on a company submissions document."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    recent: FilingColumns
    files: list[SubmissionFileRef] = Field(default_factory=list)


class CompanySubmissions(BaseModel):
    """The company submissions index at ``data.sec.gov``."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    cik: StrictStr
    name: StrictStr
    tickers: list[StrictStr] = Field(default_factory=list)
    filings: SubmissionFilings


@dataclass(frozen=True)
class FilingRecord:
    """One 10-K, 10-Q, 8-K, or amendment selected from submissions."""

    cik: str
    accession_number: str
    form_type: str
    filing_date: date
    period_of_report: date | None
    acceptance: AcceptanceTimestamp
    primary_document: str
    archive_url: str
    source_name: str


@dataclass(frozen=True)
class PrimaryDocument:
    """The original bytes of one primary filing document."""

    filing: FilingRecord
    body: bytes
    sha256: str
    byte_count: int
    content_type: str | None
    from_cache: bool


@dataclass(frozen=True)
class SubmissionDiscovery:
    """Filings selected for one CIK, plus which older files were read."""

    cik: str
    company_name: str
    tickers: tuple[str, ...]
    filings: tuple[FilingRecord, ...]
    older_files_fetched: tuple[str, ...]
    older_files_skipped: tuple[str, ...]
    recent_filing_count: int
    recent_oldest_filing_date: date | None


def parse_since(value: str) -> date:
    """Return the inclusive filing-date cutoff for ``--since``.

    A four-digit year means January 1 of that year. ``2019-02-21`` is that
    calendar date.
    """
    if not isinstance(value, str):
        raise TypeError("since must be a year or YYYY-MM-DD")
    if _SINCE_YEAR.fullmatch(value):
        try:
            return date(int(value), 1, 1)
        except ValueError as exc:
            raise ValueError("since is not a calendar date") from exc
    if _DATE.fullmatch(value) is None:
        raise ValueError("since must be a year or YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("since is not a calendar date") from exc


def same_filing(left: FilingRecord, right: FilingRecord) -> bool:
    """Return whether two rows are the same submission, ignoring which file listed them."""
    return (
        left.cik == right.cik
        and left.accession_number == right.accession_number
        and left.form_type == right.form_type
        and left.filing_date == right.filing_date
        and left.period_of_report == right.period_of_report
        and left.acceptance == right.acceptance
        and left.primary_document == right.primary_document
        and left.archive_url == right.archive_url
    )


def discover_filings(
    client: SecHttpClient,
    cik: str,
    *,
    since: date | None,
) -> SubmissionDiscovery:
    """Read recent and overlapping older submissions files for one CIK.

    A filing is included when its form is a target form and its filing date is
    on or after ``since``. ``since`` of ``None`` includes every target form.
    ``filingTo`` before ``since`` skips that older file. A missing ``filingTo``
    does not skip it. The bytes returned by the client are parsed as received.
    """
    cutoff = _require_since(since)
    normalized = normalize_cik(cik)
    index = _read_model(
        client.get(submissions_index_url(normalized)).body,
        CompanySubmissions,
        label="submissions index",
    )
    if normalize_cik(index.cik) != normalized:
        raise SecSubmissionError("submissions CIK does not match the requested CIK")
    if index.name.strip() == "":
        raise SecSubmissionError("submissions company name is empty")
    _require_aligned(index.filings.recent, source_name="recent")
    recent_dates = _audit_columns(index.filings.recent, source_name="recent")
    found: dict[str, FilingRecord] = {}
    order: list[str] = []
    _collect(
        index.filings.recent,
        source_name="recent",
        cik=normalized,
        since=cutoff,
        found=found,
        order=order,
    )
    fetched: list[str] = []
    skipped: list[str] = []
    for ref in index.filings.files:
        name = validate_submission_filename(ref.name, cik=normalized)
        start = _file_bound(ref.filing_from, name=name, field="filingFrom")
        end = _file_bound(ref.filing_to, name=name, field="filingTo")
        if start is not None and end is not None and start > end:
            raise SecSubmissionError(f"{name} filingFrom is after filingTo")
        if not _overlaps_since(end, cutoff):
            skipped.append(name)
            continue
        fetched.append(name)
        columns = _read_model(
            client.get(older_submissions_url(name, cik=normalized)).body,
            FilingColumns,
            label=name,
        )
        _require_aligned(columns, source_name=name)
        _audit_columns(columns, source_name=name)
        if ref.filing_count is not None and ref.filing_count != len(columns.form):
            raise SecSubmissionError(
                f"{name} declares {ref.filing_count} filings but contains {len(columns.form)} rows"
            )
        _collect(
            columns,
            source_name=name,
            cik=normalized,
            since=cutoff,
            found=found,
            order=order,
        )
    return SubmissionDiscovery(
        cik=normalized,
        company_name=index.name,
        tickers=tuple(index.tickers),
        filings=tuple(found[accession] for accession in order),
        older_files_fetched=tuple(fetched),
        older_files_skipped=tuple(skipped),
        recent_filing_count=len(index.filings.recent.form),
        recent_oldest_filing_date=min(recent_dates) if recent_dates else None,
    )


def download_primary_documents(
    client: SecHttpClient,
    filings: Sequence[FilingRecord],
) -> tuple[PrimaryDocument, ...]:
    """Download each primary document and keep the response bytes and SHA-256.

    The archive URL is rebuilt from the CIK, accession, and primaryDocument.
    An empty body is rejected. A repeated accession with the same filing and
    the same hash is downloaded once.
    """
    downloaded: dict[str, PrimaryDocument] = {}
    ordered: list[PrimaryDocument] = []
    for filing in filings:
        current = _download_one(client, filing)
        previous = downloaded.get(filing.accession_number)
        if previous is None:
            downloaded[filing.accession_number] = current
            ordered.append(current)
            continue
        if previous.sha256 != current.sha256 or not same_filing(previous.filing, filing):
            raise FilingConflictError(
                filing.accession_number,
                "the same accession has two different primary documents",
            )
    return tuple(ordered)


def _download_one(client: SecHttpClient, filing: FilingRecord) -> PrimaryDocument:
    url = archive_document_url(filing.cik, filing.accession_number, filing.primary_document)
    if url != filing.archive_url:
        raise SecIdentityError("archive URL does not match the filing identity")
    response = client.get(url)
    if response.body == b"":
        raise SecSubmissionError(f"primary document is empty: {url}")
    digest = hashlib.sha256(response.body).hexdigest()
    if digest != response.sha256:
        raise CacheIntegrityError("downloaded filing does not match its SHA-256", url=url)
    return PrimaryDocument(
        filing=filing,
        body=response.body,
        sha256=digest,
        byte_count=len(response.body),
        content_type=response.content_type,
        from_cache=response.from_cache,
    )


def _collect(
    columns: FilingColumns,
    *,
    source_name: str,
    cik: str,
    since: date | None,
    found: dict[str, FilingRecord],
    order: list[str],
) -> None:
    for index, form in enumerate(columns.form):
        if form not in TARGET_FORMS:
            if form.strip() in TARGET_FORMS:
                raise SecSubmissionError(
                    f"{source_name} form type {form!r} is not an exact SEC form code"
                )
            continue
        filing_date = _parse_date(
            columns.filing_date[index],
            source_name=source_name,
            index=index,
            form=form,
            field="filingDate",
        )
        if since is not None and filing_date < since:
            continue
        accession = validate_accession(columns.accession_number[index])
        primary = validate_primary_document(columns.primary_document[index])
        acceptance = parse_acceptance_datetime(columns.acceptance_date_time[index])
        period = _report_date(
            columns.report_date[index],
            source_name=source_name,
            index=index,
            form=form,
        )
        record = FilingRecord(
            cik=cik,
            accession_number=accession,
            form_type=form,
            filing_date=filing_date,
            period_of_report=period,
            acceptance=acceptance,
            primary_document=primary,
            archive_url=archive_document_url(cik, accession, primary),
            source_name=source_name,
        )
        previous = found.get(accession)
        if previous is None:
            found[accession] = record
            order.append(accession)
            continue
        if not same_filing(previous, record):
            raise FilingConflictError(
                accession,
                "the same accession appears with different filing metadata",
            )


def _audit_columns(columns: FilingColumns, *, source_name: str) -> tuple[date, ...]:
    dates: list[date] = []
    for index, form in enumerate(columns.form):
        dates.append(
            _parse_date(
                columns.filing_date[index],
                source_name=source_name,
                index=index,
                form=form,
                field="filingDate",
            )
        )
        report = columns.report_date[index]
        if report != "":
            _parse_date(
                report,
                source_name=source_name,
                index=index,
                form=form,
                field="reportDate",
            )
        validate_accession(columns.accession_number[index])
    return tuple(dates)


def _require_aligned(columns: FilingColumns, *, source_name: str) -> None:
    lengths = {
        "accessionNumber": len(columns.accession_number),
        "filingDate": len(columns.filing_date),
        "reportDate": len(columns.report_date),
        "acceptanceDateTime": len(columns.acceptance_date_time),
        "form": len(columns.form),
        "primaryDocument": len(columns.primary_document),
    }
    if len(set(lengths.values())) != 1:
        detail = ", ".join(f"{key}={value}" for key, value in lengths.items())
        raise SecSubmissionError(f"{source_name} filing columns differ in length: {detail}")


def _read_model(payload: bytes, model: type[_ModelT], *, label: str) -> _ModelT:
    if not isinstance(payload, bytes) or payload == b"":
        raise SecSubmissionError(f"{label} is empty")
    try:
        return model.model_validate_json(payload)
    except (ValidationError, UnicodeDecodeError) as exc:
        raise SecSubmissionError(f"{label} does not match the SEC submissions shape") from exc


def _require_since(since: date | None) -> date | None:
    if since is None:
        return None
    if isinstance(since, datetime) or not isinstance(since, date):
        raise TypeError("since must be a datetime.date")
    return since


def _overlaps_since(end: date | None, since: date | None) -> bool:
    if since is None or end is None:
        return True
    return end >= since


def _file_bound(value: str | None, *, name: str, field: str) -> date | None:
    if value is None or value == "":
        return None
    if _DATE.fullmatch(value) is None:
        raise SecSubmissionError(f"{name} {field} is not YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SecSubmissionError(f"{name} {field} is not a calendar date") from exc


def _report_date(value: str, *, source_name: str, index: int, form: str) -> date | None:
    if value == "":
        return None
    return _parse_date(value, source_name=source_name, index=index, form=form, field="reportDate")


def _parse_date(value: str, *, source_name: str, index: int, form: str, field: str) -> date:
    if _DATE.fullmatch(value) is None:
        raise SecSubmissionError(
            f"{source_name} {field} at index {index} ({form}) is not YYYY-MM-DD"
        )
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SecSubmissionError(
            f"{source_name} {field} at index {index} ({form}) is not a calendar date"
        ) from exc
