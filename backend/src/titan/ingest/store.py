"""Write filing metadata through the Phase 1 tables.

Document rows are inserted. A repeated accession uses
``ON CONFLICT DO NOTHING`` and is kept only when the stored metadata matches
the submission. An amendment has its own accession, so it is a separate row.
The confirmation read goes through ``PointInTimeRepository``. This module does
not select from ``documents`` itself, and it does not commit.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from titan.ingest.errors import CacheIntegrityError, FilingConflictError, SecSubmissionError
from titan.ingest.sec_transport import SecHttpClient
from titan.ingest.submissions import (
    PrimaryDocument,
    SubmissionDiscovery,
    discover_filings,
    download_primary_documents,
    same_filing,
)
from titan.models.company import Company
from titan.models.document import Document
from titan.pit.repository import PointInTimeRepository
from titan.schemas.document import DocumentCreate, DocumentRead


@dataclass(frozen=True)
class StoredFiling:
    """One document row written or confirmed for an accession."""

    document_id: UUID
    company_id: UUID
    accession_number: str
    form_type: str
    filed_at: datetime
    known_at: datetime
    period_of_report: date | None
    acceptance_original: str
    acceptance_precision: int
    primary_document: str
    archive_url: str
    sha256: str
    byte_count: int
    inserted: bool


@dataclass(frozen=True)
class IngestResult:
    """Discovery, original primary documents, and the document rows."""

    company_id: UUID
    discovery: SubmissionDiscovery
    documents: tuple[PrimaryDocument, ...]
    stored: tuple[StoredFiling, ...]


def ingest_primary_filings(
    session: Session,
    client: SecHttpClient,
    *,
    cik: str,
    since: date | None,
    ticker: str | None = None,
) -> IngestResult:
    """Discover, download, and store primary filings for one CIK.

    The caller commits. On failure the caller rolls the session back. Company
    name and ticker are written only when the CIK is new.
    """
    discovery = discover_filings(client, cik, since=since)
    documents = download_primary_documents(client, discovery.filings)
    company_id = ensure_company(
        session,
        cik=discovery.cik,
        name=discovery.company_name,
        ticker=_choose_ticker(ticker, discovery.tickers),
    )
    stored = store_primary_documents(session, company_id, documents)
    return IngestResult(
        company_id=company_id,
        discovery=discovery,
        documents=documents,
        stored=stored,
    )


def ensure_company(
    session: Session,
    *,
    cik: str,
    name: str,
    ticker: str | None,
) -> UUID:
    """Return the company id for ``cik``, inserting it when it is absent."""
    if name.strip() == "":
        raise SecSubmissionError("submissions company name is empty")
    if ticker is not None and ticker.strip() == "":
        raise SecSubmissionError("ticker must not be empty")
    existing = session.scalar(select(Company).where(Company.cik == cik))
    if existing is not None:
        return existing.id
    company = Company(cik=cik, name=name, ticker=ticker)
    session.add(company)
    session.flush()
    return company.id


def store_primary_documents(
    session: Session,
    company_id: UUID,
    documents: Sequence[PrimaryDocument],
) -> tuple[StoredFiling, ...]:
    """Insert document metadata for each primary filing.

    The raw bytes stay in the SEC response cache. The row stores the accession,
    form, filing date, report period, and the UTC acceptance time. A second
    insert of the same accession changes nothing when the stored row matches.
    A different payload for that accession raises ``FilingConflictError`` and
    does not update the row.
    """
    unique = _unique_documents(documents)
    if not unique:
        return ()
    drafts = [_draft(company_id, document) for document in unique]
    inserted_ids: list[UUID | None] = []
    for draft in drafts:
        inserted_ids.append(_insert(session, draft))
    session.flush()
    # The writer confirms rows it just stored. This cutoff is not an agent as_of.
    visible = PointInTimeRepository(session).visible_documents(
        company_id=company_id,
        as_of=datetime(9999, 1, 1, tzinfo=UTC),
    )
    by_accession = {row.accession_number: row for row in visible}
    stored: list[StoredFiling] = []
    for document, draft, inserted_id in zip(unique, drafts, inserted_ids, strict=True):
        row = by_accession.get(draft.accession_number)
        if row is None:
            raise FilingConflictError(
                draft.accession_number,
                "accession is already stored for a different company or was not inserted",
            )
        if inserted_id is not None and inserted_id != row.id:
            raise FilingConflictError(
                draft.accession_number,
                "database returned an unexpected document id",
            )
        if not _same_row(draft, row):
            raise FilingConflictError(
                draft.accession_number,
                "stored filing metadata does not match this submission",
            )
        filing = document.filing
        stored.append(
            StoredFiling(
                document_id=row.id,
                company_id=row.company_id,
                accession_number=row.accession_number,
                form_type=row.form_type,
                filed_at=row.filed_at,
                known_at=row.known_at,
                period_of_report=row.period_of_report,
                acceptance_original=filing.acceptance.original,
                acceptance_precision=filing.acceptance.precision,
                primary_document=filing.primary_document,
                archive_url=filing.archive_url,
                sha256=document.sha256,
                byte_count=document.byte_count,
                inserted=inserted_id is not None,
            )
        )
    return tuple(stored)


def _unique_documents(documents: Sequence[PrimaryDocument]) -> tuple[PrimaryDocument, ...]:
    found: dict[str, PrimaryDocument] = {}
    order: list[str] = []
    for document in documents:
        _require_bytes(document)
        accession = document.filing.accession_number
        previous = found.get(accession)
        if previous is None:
            found[accession] = document
            order.append(accession)
            continue
        if previous.sha256 != document.sha256 or not same_filing(previous.filing, document.filing):
            raise FilingConflictError(
                accession,
                "the same accession has two different primary documents",
            )
    return tuple(found[accession] for accession in order)


def _require_bytes(document: PrimaryDocument) -> None:
    if document.body == b"":
        raise SecSubmissionError(f"primary document is empty: {document.filing.archive_url}")
    digest = hashlib.sha256(document.body).hexdigest()
    if digest != document.sha256 or document.byte_count != len(document.body):
        raise CacheIntegrityError(
            "primary document does not match its SHA-256",
            url=document.filing.archive_url,
        )


def _draft(company_id: UUID, document: PrimaryDocument) -> DocumentCreate:
    filing = document.filing
    return DocumentCreate(
        company_id=company_id,
        accession_number=filing.accession_number,
        form_type=filing.form_type,
        filed_at=datetime(
            filing.filing_date.year,
            filing.filing_date.month,
            filing.filing_date.day,
            tzinfo=UTC,
        ),
        known_at=filing.acceptance.utc,
        period_of_report=filing.period_of_report,
    )


def _insert(session: Session, draft: DocumentCreate) -> UUID | None:
    new_id = uuid4()
    statement = (
        pg_insert(Document)
        .values(
            id=new_id,
            company_id=draft.company_id,
            accession_number=draft.accession_number,
            form_type=draft.form_type,
            filed_at=draft.filed_at,
            known_at=draft.known_at,
            period_of_report=draft.period_of_report,
        )
        .on_conflict_do_nothing(constraint="uq_documents_accession_number")
        .returning(Document.id)
    )
    inserted_id = session.execute(statement).scalar_one_or_none()
    if inserted_id is None:
        return None
    if not isinstance(inserted_id, UUID) or inserted_id != new_id:
        raise FilingConflictError(
            draft.accession_number,
            "database returned an unexpected document id",
        )
    return inserted_id


def _same_row(draft: DocumentCreate, row: DocumentRead) -> bool:
    return (
        row.company_id == draft.company_id
        and row.accession_number == draft.accession_number
        and row.form_type == draft.form_type
        and row.filed_at == draft.filed_at
        and row.known_at == draft.known_at
        and row.period_of_report == draft.period_of_report
    )


def _choose_ticker(explicit: str | None, published: tuple[str, ...]) -> str | None:
    if explicit is not None:
        if explicit.strip() == "":
            raise SecSubmissionError("ticker must not be empty")
        return explicit
    for ticker in published:
        if ticker.strip() != "":
            return ticker
    return None
