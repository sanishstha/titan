"""Metadata checks for the Phase 1 tables. These do not need a database."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import DateTime, Numeric, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import configure_mappers
from sqlalchemy.schema import CreateIndex

from titan.common.dimensions import hash_dimensions
from titan.models import Base, Fact
from titan.models.fact import (
    FactIdentityError,
    FactImmutableError,
    _check_fact_identity,
    _reject_fact_update,
)
from titan.models.job import Job


def test_six_tables_are_registered() -> None:
    configure_mappers()
    assert set(Base.metadata.tables) == {
        "companies",
        "documents",
        "facts",
        "claims",
        "jobs",
        "traces",
    }
    columns = Base.metadata.tables["companies"].columns
    assert next(iter(columns)).name == "id"


def test_fact_money_time_and_as_of_index() -> None:
    value = Fact.__table__.c.value
    assert isinstance(value.type, Numeric)
    assert value.type.precision == 38
    assert value.type.scale == 10
    known_at = Fact.__table__.c.known_at
    assert isinstance(known_at.type, DateTime)
    assert known_at.type.timezone is True
    assert known_at.nullable is False

    identity = next(
        constraint
        for constraint in Fact.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_facts_source_identity"
    )
    assert identity.dialect_options["postgresql"]["nulls_not_distinct"] is True

    index = next(item for item in Fact.__table__.indexes if item.name == "ix_facts_as_of")
    ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert "known_at DESC" in ddl


def test_queued_jobs_use_a_partial_index() -> None:
    index = next(item for item in Job.__table__.indexes if item.name == "ix_jobs_queued_run_after")
    ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert "status = 'queued'" in ddl
    unique = next(item for item in Job.__table__.indexes if item.name == "uq_jobs_idempotency_key")
    assert unique.unique is True
    unique_ddl = str(CreateIndex(unique).compile(dialect=postgresql.dialect()))
    assert "idempotency_key IS NOT NULL" in unique_ddl


def test_fact_insert_requires_matching_hash_and_update_is_rejected() -> None:
    fact = Fact(
        company_id=uuid4(),
        document_id=uuid4(),
        taxonomy="us-gaap",
        concept="Assets",
        value=Decimal(10),
        unit="USD",
        period_type="instant",
        period_end=date(2023, 12, 31),
        known_at=datetime(2024, 2, 1, tzinfo=UTC),
        dimensions={},
        dimensions_hash=hash_dimensions({}),
    )
    _check_fact_identity(None, None, fact)  # type: ignore[arg-type]
    fact.dimensions_hash = "0" * 64
    with pytest.raises(FactIdentityError):
        _check_fact_identity(None, None, fact)  # type: ignore[arg-type]
    with pytest.raises(FactImmutableError):
        _reject_fact_update(None, None, fact)  # type: ignore[arg-type]
