"""Append-only XBRL facts. A restatement is a new row, never an update."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    Uuid,
    desc,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from titan.common.dimensions import hash_dimensions
from titan.models.base import Base, KnownAtMixin, uuid_pk

_PERIOD_CHECK = """
(
    period_type = 'duration'
    AND period_start IS NOT NULL
    AND period_start <= period_end
)
OR
(
    period_type = 'instant'
    AND period_start IS NULL
)
"""


class FactImmutableError(RuntimeError):
    """Raised when code tries to change a stored fact version."""


class FactIdentityError(ValueError):
    """Raised when a fact's dimension hash does not match its dimensions."""


class Fact(KnownAtMixin, Base):
    """One reported numeric fact from one filing.

    The same logical fact reported again in a later filing is a new row:
    new ``document_id``, new ``value``, new ``known_at``. The earlier row stays.
    Source identity is ``document_id`` plus taxonomy, concept, unit, period,
    and ``dimensions_hash``. ``NULLS NOT DISTINCT`` makes two instant facts
    with a null ``period_start`` collide, so a reload cannot insert a second copy.
    """

    __tablename__ = "facts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "company_id"],
            ["documents.id", "documents.company_id"],
            name="fk_facts_document_company",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "document_id",
            "taxonomy",
            "concept",
            "unit",
            "period_start",
            "period_end",
            "dimensions_hash",
            name="uq_facts_source_identity",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(_PERIOD_CHECK, name="period"),
        CheckConstraint(
            "dimensions_hash ~ '^[0-9a-f]{64}$'",
            name="dimensions_hash",
        ),
        CheckConstraint(
            "jsonb_typeof(dimensions) = 'object'",
            name="dimensions_object",
        ),
        CheckConstraint(
            "char_length(btrim(taxonomy)) > 0",
            name="taxonomy_nonempty",
        ),
        CheckConstraint(
            "char_length(btrim(concept)) > 0",
            name="concept_nonempty",
        ),
        CheckConstraint("char_length(btrim(unit)) > 0", name="unit_nonempty"),
        CheckConstraint(
            "fiscal_year IS NULL OR (fiscal_year >= 1900 AND fiscal_year <= 2200)",
            name="fiscal_year",
        ),
        CheckConstraint(
            "fiscal_period IS NULL OR fiscal_period IN ('FY', 'Q1', 'Q2', 'Q3', 'Q4')",
            name="fiscal_period",
        ),
        Index(
            "ix_facts_as_of",
            "company_id",
            "taxonomy",
            "concept",
            "unit",
            "period_end",
            "period_start",
            "dimensions_hash",
            desc("known_at"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("companies.id", name="fk_facts_company_id", ondelete="RESTRICT"),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    taxonomy: Mapped[str] = mapped_column(Text, nullable=False)
    concept: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(38, 10), nullable=False)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    decimals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    period_type: Mapped[str] = mapped_column(Text, nullable=False)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    fiscal_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fiscal_period: Mapped[str | None] = mapped_column(Text, nullable=True)
    dimensions: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    dimensions_hash: Mapped[str] = mapped_column(Text, nullable=False)


def _require_dimension_hash(target: Fact) -> None:
    dimensions = target.dimensions
    if not isinstance(dimensions, dict):
        raise FactIdentityError("dimensions must be a JSON object")
    try:
        expected = hash_dimensions(dimensions)
    except TypeError as exc:
        raise FactIdentityError(str(exc)) from exc
    if target.dimensions_hash != expected:
        raise FactIdentityError(
            "dimensions_hash does not match the SHA-256 of canonical dimensions JSON"
        )


@event.listens_for(Fact, "before_insert")
def _check_fact_identity(_mapper: Mapper[Fact], _connection: Connection, target: Fact) -> None:
    _require_dimension_hash(target)


@event.listens_for(Fact, "before_update")
def _reject_fact_update(_mapper: Mapper[Fact], _connection: Connection, _target: Fact) -> None:
    raise FactImmutableError(
        "fact rows are append-only; insert a new row for a restatement"
    )
