"""Incremental primary-filing ingestion.

Discovery reads the submissions index and only the older files that overlap
``since``. Each selected filing is downloaded and upserted on its own. A
download or store error is recorded and the remaining filings continue. When
``commit_each`` is set, the company row and every successful filing are
committed before the next filing starts, so a crash keeps completed rows and
the next run treats them as unchanged. Archive bytes stay in the raw cache, so
the retry does not depend on the database holding the file body.

The confirmation read goes through ``PointInTimeRepository``. This module does
not select from ``documents``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from titan.ingest.errors import FilingConflictError, SecSubmissionError, SecTransportError
from titan.ingest.sec_transport import SecHttpClient
from titan.ingest.store import StoredFiling, ensure_company, store_primary_documents
from titan.ingest.submissions import (
    FilingRecord,
    SubmissionDiscovery,
    discover_filings,
    download_primary_documents,
)
from titan.pit.repository import PointInTimeRepository
from titan.schemas.document import DocumentRead

# Writer cutoff used to confirm rows just stored. This is not an agent as_of.
_WRITER_AS_OF = datetime(9999, 1, 1, tzinfo=UTC)

IngestStage = Literal["download", "store"]


@dataclass(frozen=True)
class IngestFailure:
    """One filing that was not stored on this run."""

    accession_number: str
    archive_url: str
    stage: IngestStage
    error_type: str
    message: str


IngestProgress = Callable[[StoredFiling | IngestFailure], None]


@dataclass(frozen=True)
class IngestCounts:
    """How many filings were found and what happened to the ones this run tried."""

    discovered: int
    inserted: int
    unchanged: int
    failed: int


@dataclass(frozen=True)
class IncrementalIngestResult:
    """Counts, structured failures, and the document-row fingerprint."""

    company_id: UUID
    discovery: SubmissionDiscovery
    stored: tuple[StoredFiling, ...]
    failures: tuple[IngestFailure, ...]
    counts: IngestCounts
    document_count: int
    row_sha256: str
    content_sha256: str


def ingest_incrementally(
    session: Session,
    client: SecHttpClient,
    *,
    cik: str,
    since: date,
    ticker: str | None = None,
    limit: int | None = None,
    commit_each: bool = False,
    progress: IngestProgress | None = None,
) -> IncrementalIngestResult:
    """Discover filings on or after ``since`` and upsert them one at a time.

    ``limit`` stops after that many filings. Filings past the limit are counted
    as discovered and are not downloaded. ``since`` is required so this entry
    point does not walk every older submissions file. The caller that sets
    ``commit_each`` must pass a session dedicated to this run: each commit
    persists the whole session.
    """
    cutoff = _require_since(since)
    bounded = _require_limit(limit)
    if ticker is not None and ticker.strip() == "":
        raise SecSubmissionError("ticker must not be empty")
    discovery = discover_filings(client, cik, since=cutoff)
    company_id = ensure_company(
        session,
        cik=discovery.cik,
        name=discovery.company_name,
        ticker=_choose_ticker(ticker, discovery.tickers),
    )
    if commit_each:
        session.commit()
    selected = discovery.filings if bounded is None else discovery.filings[:bounded]
    stored: list[StoredFiling] = []
    failures: list[IngestFailure] = []
    for filing in selected:
        outcome = _ingest_one(
            session,
            client,
            company_id=company_id,
            filing=filing,
            commit_each=commit_each,
        )
        if isinstance(outcome, IngestFailure):
            failures.append(outcome)
        else:
            stored.append(outcome)
        if progress is not None:
            progress(outcome)
    counts = IngestCounts(
        discovered=len(discovery.filings),
        inserted=sum(1 for row in stored if row.inserted),
        unchanged=sum(1 for row in stored if not row.inserted),
        failed=len(failures),
    )
    if counts.inserted + counts.unchanged + counts.failed != len(selected):
        raise RuntimeError("ingestion counts do not match the filings that were attempted")
    rows = PointInTimeRepository(session).visible_documents(
        company_id=company_id,
        as_of=_WRITER_AS_OF,
    )
    return IncrementalIngestResult(
        company_id=company_id,
        discovery=discovery,
        stored=tuple(stored),
        failures=tuple(failures),
        counts=counts,
        document_count=len(rows),
        row_sha256=row_sha256(rows),
        content_sha256=content_sha256(stored),
    )


def content_sha256(stored: Sequence[StoredFiling]) -> str:
    """Hash accession, form, acceptance time, and primary-document SHA-256."""
    lines = [
        "|".join(
            (
                row.accession_number,
                row.form_type,
                row.known_at.isoformat(),
                row.sha256,
                str(row.byte_count),
            )
        )
        for row in sorted(stored, key=lambda item: item.accession_number)
    ]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def row_sha256(rows: Sequence[DocumentRead]) -> str:
    """Hash the visible document rows for one company."""
    lines = [
        "|".join(
            (
                str(row.id),
                row.accession_number,
                row.form_type,
                row.filed_at.isoformat(),
                row.known_at.isoformat(),
                "" if row.period_of_report is None else row.period_of_report.isoformat(),
            )
        )
        for row in sorted(rows, key=lambda item: (item.accession_number, str(item.id)))
    ]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _ingest_one(
    session: Session,
    client: SecHttpClient,
    *,
    company_id: UUID,
    filing: FilingRecord,
    commit_each: bool,
) -> StoredFiling | IngestFailure:
    try:
        downloaded = download_primary_documents(client, (filing,))
    except SecTransportError as exc:
        return _failure(filing, "download", exc)
    if len(downloaded) != 1:
        raise FilingConflictError(filing.accession_number, "expected one primary document")
    try:
        with session.begin_nested():
            stored_rows = store_primary_documents(session, company_id, downloaded)
    except SecTransportError as exc:
        return _failure(filing, "store", exc)
    if len(stored_rows) != 1:
        raise FilingConflictError(filing.accession_number, "expected one stored filing")
    if commit_each:
        session.commit()
    return stored_rows[0]


def _failure(filing: FilingRecord, stage: IngestStage, exc: SecTransportError) -> IngestFailure:
    return IngestFailure(
        accession_number=filing.accession_number,
        archive_url=filing.archive_url,
        stage=stage,
        error_type=type(exc).__name__,
        message=str(exc),
    )


def _require_since(since: date) -> date:
    if isinstance(since, datetime) or not isinstance(since, date):
        raise TypeError("since must be a datetime.date")
    return since


def _require_limit(limit: int | None) -> int | None:
    if limit is None:
        return None
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    return limit


def _choose_ticker(explicit: str | None, published: tuple[str, ...]) -> str | None:
    if explicit is not None:
        if explicit.strip() == "":
            raise SecSubmissionError("ticker must not be empty")
        return explicit
    for ticker in published:
        if ticker.strip() != "":
            return ticker
    return None
