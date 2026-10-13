"""Point-in-time reads of documents and financial facts.

This module is the application read path for ``documents`` and ``facts``.
Services and agents ask it for rows. They do not query those tables.

Visibility
----------
Every method takes a timezone-aware ``as_of``. A row is visible when
``known_at <= as_of``. A later ``known_at`` is excluded. An equal timestamp
is included. ``as_of`` is converted to UTC before the comparison. ``filed_at``
and ``period_of_report`` are filing metadata and do not hide or show a row.

A fact is visible only when the fact and its source document are both visible
for the same company. The fact's ``known_at`` is copied from the document at
insert, and the join still applies the cutoff to the document so a mismatched
pair cannot leak.

The repository does not remember ``as_of``. Each call passes its own cutoff.
Results are frozen read models, copied while the session is open, so a later
session change cannot widen that cutoff.

Versions
--------
``visible_documents`` returns every filing for one company that was already
public, oldest ``known_at`` first.

``visible_fact_versions`` returns every stored fact version that was already
public. An original filing and a later restatement are both returned when
both were public by ``as_of``. This method does not collapse versions and it
does not write.

``latest_visible_fact`` returns the one visible version for an explicit
logical fact. The logical fact is taxonomy, concept, unit, reporting period
(``period_end`` and ``period_start``), and ``dimensions_hash``. The greatest
``known_at`` wins. A shared ``known_at`` is broken by accession number, then
fact id, both descending. A restatement therefore replaces the original in
this view only after the restatement itself is visible. The original row
stays in the database and in ``visible_fact_versions``.

Ordering of ``visible_fact_versions`` is the logical key, then ``known_at``
ascending, then accession number, then fact id. Text columns in that order
use the C collation, so the sequence is byte order and stays the same if the
database locale changes. A null ``period_start`` sorts after a date. Within
one logical key the last row is the same row ``latest_visible_fact`` returns.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from titan.models.document import Document
from titan.models.fact import Fact
from titan.schemas.document import DocumentRead
from titan.schemas.fact import FactRead

_DIMENSIONS_HASH = re.compile(r"^[0-9a-f]{64}$")


class AsOfError(ValueError):
    """Raised when ``as_of`` has no timezone offset."""


class ReportingPeriod:
    """Calendar span of one fact.

    An instant fact, such as assets, sets ``period_end`` and leaves
    ``period_start`` unset. A duration fact, such as revenue, sets both dates
    and requires ``period_start <= period_end``.
    """

    __slots__ = ("period_end", "period_start")

    def __init__(self, period_end: date, period_start: date | None = None) -> None:
        if period_start is not None and period_start > period_end:
            raise ValueError("period_start must be on or before period_end")
        self.period_end = period_end
        self.period_start = period_start

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ReportingPeriod):
            return NotImplemented
        return (
            self.period_end == other.period_end and self.period_start == other.period_start
        )

    def __hash__(self) -> int:
        return hash((self.period_end, self.period_start))


class PointInTimeRepository:
    """Read documents and facts as of one caller-supplied cutoff per call."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def visible_documents(
        self,
        *,
        company_id: UUID,
        as_of: datetime,
    ) -> tuple[DocumentRead, ...]:
        """Return filings whose acceptance time is at or before ``as_of``.

        Order is ``known_at``, accession number, then document id, all
        ascending.
        """
        statement = _visible_documents_statement(company_id, as_of)
        rows = self._session.scalars(statement).all()
        loaded: list[DocumentRead] = [DocumentRead.model_validate(row) for row in rows]
        return tuple(loaded)

    def visible_fact_versions(
        self,
        *,
        company_id: UUID,
        as_of: datetime,
    ) -> tuple[FactRead, ...]:
        """Return every fact version public by ``as_of``, including restatements.

        A fact whose source document is still in the future is omitted.
        Order is the logical fact key, then ``known_at``, accession number,
        and fact id, all ascending.
        """
        statement = _visible_fact_versions_statement(company_id, as_of)
        rows = self._session.scalars(statement).all()
        loaded: list[FactRead] = [FactRead.model_validate(row) for row in rows]
        return tuple(loaded)

    def latest_visible_fact(
        self,
        *,
        company_id: UUID,
        as_of: datetime,
        concept: str,
        unit: str,
        period: ReportingPeriod,
        taxonomy: str,
        dimensions_hash: str,
    ) -> FactRead | None:
        """Return the newest visible version of one explicit logical fact.

        ``concept``, ``unit``, and ``period`` identify the reported number.
        ``taxonomy`` and ``dimensions_hash`` are required as well: a concept
        name belongs to a taxonomy, and a segment hash is a different fact
        from the company total. Returns ``None`` when no version of that fact
        was public by ``as_of``.
        """
        statement = _latest_visible_fact_statement(
            company_id=company_id,
            as_of=as_of,
            concept=concept,
            unit=unit,
            period=period,
            taxonomy=taxonomy,
            dimensions_hash=dimensions_hash,
        )
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None
        return FactRead.model_validate(row[0])


def _logical_key() -> tuple[Any, ...]:
    """Sort and DISTINCT ON prefix. Text uses the C collation."""
    return (
        Fact.company_id,
        Fact.taxonomy.collate("C"),
        Fact.concept.collate("C"),
        Fact.unit.collate("C"),
        Fact.period_end,
        Fact.period_start,
        Fact.dimensions_hash.collate("C"),
    )


def _cutoff(as_of: datetime) -> datetime:
    if as_of.utcoffset() is None:
        raise AsOfError("as_of must be timezone-aware")
    return as_of.astimezone(UTC)


def _require_label(value: str, field_name: str) -> None:
    if value.strip() == "":
        raise ValueError(f"{field_name} must be non-empty")


def _require_identity(taxonomy: str, concept: str, unit: str, dimensions_hash: str) -> None:
    _require_label(taxonomy, "taxonomy")
    _require_label(concept, "concept")
    _require_label(unit, "unit")
    if _DIMENSIONS_HASH.fullmatch(dimensions_hash) is None:
        raise ValueError("dimensions_hash must be 64 lowercase hex characters")


def _source_document_match() -> ColumnElement[bool]:
    return and_(
        Fact.document_id == Document.id,
        Fact.company_id == Document.company_id,
    )


def _period_match(period: ReportingPeriod) -> ColumnElement[bool]:
    if period.period_start is None:
        start: ColumnElement[bool] = Fact.period_start.is_(None)
    else:
        start = Fact.period_start == period.period_start
    return and_(start, Fact.period_end == period.period_end)


def _visible_documents_statement(company_id: UUID, as_of: datetime) -> Select[Document]:
    cutoff = _cutoff(as_of)
    return (
        select(Document)
        .where(
            Document.company_id == company_id,
            Document.known_at <= cutoff,
        )
        .order_by(
            Document.known_at.asc(),
            Document.accession_number.collate("C").asc(),
            Document.id.asc(),
        )
        .execution_options(populate_existing=True)
    )


def _visible_fact_versions_statement(company_id: UUID, as_of: datetime) -> Select[Fact]:
    cutoff = _cutoff(as_of)
    return (
        select(Fact)
        .join(Document, _source_document_match())
        .where(
            Fact.company_id == company_id,
            Fact.known_at <= cutoff,
            Document.known_at <= cutoff,
        )
        .order_by(
            *_logical_key(),
            Fact.known_at.asc(),
            Document.accession_number.collate("C").asc(),
            Fact.id.asc(),
        )
        .execution_options(populate_existing=True)
    )


def _latest_visible_fact_statement(
    *,
    company_id: UUID,
    as_of: datetime,
    concept: str,
    unit: str,
    period: ReportingPeriod,
    taxonomy: str,
    dimensions_hash: str,
) -> Select[Fact, str]:
    cutoff = _cutoff(as_of)
    _require_identity(taxonomy, concept, unit, dimensions_hash)
    # DISTINCT ON keeps the first row of the logical key. The ORDER BY prefix
    # matches ix_facts_as_of; accession and id break a shared known_at.
    logical_key = _logical_key()
    return (
        select(Fact, Document.accession_number)
        .join(Document, _source_document_match())
        .where(
            Fact.company_id == company_id,
            Fact.known_at <= cutoff,
            Document.known_at <= cutoff,
            Fact.taxonomy == taxonomy,
            Fact.concept == concept,
            Fact.unit == unit,
            Fact.dimensions_hash == dimensions_hash,
            _period_match(period),
        )
        .ext(distinct_on(*logical_key))
        .order_by(
            *logical_key,
            Fact.known_at.desc(),
            Document.accession_number.collate("C").desc(),
            Fact.id.desc(),
        )
        .execution_options(populate_existing=True)
    )
