"""Schema behavior against Postgres. Skips when the migration has not been applied."""

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from titan.common.dimensions import hash_dimensions
from titan.common.session import create_db_engine, create_session_factory
from titan.models import Company, Document, Fact
from titan.models.fact import FactImmutableError


@pytest.fixture
def db_session() -> Iterator[Session]:
    engine = create_db_engine()
    try:
        with engine.connect() as connection:
            found = connection.execute(text("SELECT to_regclass('public.facts')")).scalar_one()
    except SQLAlchemyError:
        engine.dispose()
        pytest.skip("Postgres is not available")
    if found is None:
        engine.dispose()
        pytest.skip("facts table is not migrated")
    session = create_session_factory(engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def test_fact_column_types(db_session: Session) -> None:
    value = db_session.execute(
        text(
            """
            SELECT data_type, numeric_precision, numeric_scale
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'facts'
              AND column_name = 'value'
            """
        )
    ).one()
    assert value.data_type == "numeric"
    assert value.numeric_precision == 38
    assert value.numeric_scale == 10
    known_at = db_session.execute(
        text(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'facts'
              AND column_name = 'known_at'
            """
        )
    ).scalar_one()
    assert known_at == "timestamp with time zone"


def _company() -> Company:
    return Company(cik=f"{uuid4().int % 10**10:010d}", ticker="TEST", name="Fixture Co")


def _filing(company_id: UUID, accession_number: str, known_at: datetime, form_type: str) -> Document:
    return Document(
        company_id=company_id,
        accession_number=accession_number,
        form_type=form_type,
        filed_at=known_at,
        known_at=known_at,
        period_of_report=date(2023, 12, 31),
    )


def _assets(company_id: UUID, document_id: UUID, value: str, known_at: datetime) -> Fact:
    return Fact(
        company_id=company_id,
        document_id=document_id,
        taxonomy="us-gaap",
        concept="Assets",
        value=Decimal(value),
        unit="USD",
        period_type="instant",
        period_end=date(2023, 12, 31),
        known_at=known_at,
        dimensions={},
        dimensions_hash=hash_dimensions({}),
    )


def test_restatement_keeps_both_versions(db_session: Session) -> None:
    company = _company()
    original_at = datetime(2024, 2, 1, tzinfo=UTC)
    restated_at = datetime(2024, 11, 1, tzinfo=UTC)
    original = _filing(company.id, f"{company.cik}-24-000010", original_at, "10-K")
    amended = _filing(company.id, f"{company.cik}-24-000099", restated_at, "10-K/A")
    first = _assets(company.id, original.id, "100.10", original_at)
    second = _assets(company.id, amended.id, "90.25", restated_at)
    db_session.add_all([company, original, amended, first, second])
    db_session.flush()

    stored = set(db_session.scalars(select(Fact.value).where(Fact.company_id == company.id)))
    assert stored == {Decimal("100.10"), Decimal("90.25")}


def test_database_rejects_fact_updates(db_session: Session) -> None:
    company = _company()
    known_at = datetime(2024, 2, 1, tzinfo=UTC)
    document = _filing(company.id, f"{company.cik}-24-000010", known_at, "10-K")
    fact = _assets(company.id, document.id, "100.10", known_at)
    db_session.add_all([company, document, fact])
    db_session.flush()
    with pytest.raises(DBAPIError, match="append-only"):
        db_session.execute(
            text("UPDATE facts SET value = 1 WHERE id = :fact_id"),
            {"fact_id": fact.id},
        )


def test_orm_rejects_fact_updates(db_session: Session) -> None:
    company = _company()
    known_at = datetime(2024, 2, 1, tzinfo=UTC)
    document = _filing(company.id, f"{company.cik}-24-000010", known_at, "10-K")
    fact = _assets(company.id, document.id, "100.10", known_at)
    db_session.add_all([company, document, fact])
    db_session.flush()
    fact.value = Decimal(1)
    with pytest.raises(FactImmutableError):
        db_session.flush()


def test_same_instant_fact_cannot_be_inserted_twice(db_session: Session) -> None:
    company = _company()
    known_at = datetime(2024, 2, 1, tzinfo=UTC)
    document = _filing(company.id, f"{company.cik}-24-000010", known_at, "10-K")
    first = _assets(company.id, document.id, "100", known_at)
    duplicate = _assets(company.id, document.id, "101", known_at)
    db_session.add_all([company, document, first, duplicate])
    with pytest.raises(IntegrityError):
        db_session.flush()
