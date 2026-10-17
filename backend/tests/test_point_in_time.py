"""Point-in-time leakage cases. Each test fails if a future row becomes visible."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from titan.common.dimensions import hash_dimensions
from titan.common.session import create_db_engine, create_session_factory
from titan.models import Company, Document, Fact
from titan.pit.repository import AsOfError, PointInTimeRepository, ReportingPeriod
from titan.schemas.fact import FactCreate, FactRead

_TOTAL = hash_dimensions({})
_FY2023 = ReportingPeriod(period_end=date(2023, 12, 31), period_start=date(2023, 1, 1))
_FY2024 = ReportingPeriod(period_end=date(2024, 12, 31), period_start=date(2024, 1, 1))


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


def _company(ticker: str) -> Company:
    return Company(cik=f"{uuid4().int % 10**10:010d}", ticker=ticker, name=f"{ticker} Co")


def _filing(company: Company, suffix: str, known_at: datetime, form_type: str = "10-K") -> Document:
    return Document(
        company_id=company.id,
        accession_number=f"{company.cik}-24-{suffix}",
        form_type=form_type,
        filed_at=known_at,
        known_at=known_at,
        period_of_report=date(2023, 12, 31),
    )


def _fact(
    company_id: UUID,
    document_id: UUID,
    known_at: datetime,
    value: str,
    *,
    unit: str = "USD",
    period_start: date | None = date(2023, 1, 1),
    period_end: date = date(2023, 12, 31),
) -> Fact:
    return Fact(
        company_id=company_id,
        document_id=document_id,
        taxonomy="us-gaap",
        concept="Revenues",
        value=Decimal(value),
        unit=unit,
        period_type="duration" if period_start is not None else "instant",
        period_start=period_start,
        period_end=period_end,
        known_at=known_at,
        dimensions={},
        dimensions_hash=_TOTAL,
    )


def _latest(
    session: Session,
    company_id: UUID,
    as_of: datetime,
    *,
    unit: str = "USD",
    period: ReportingPeriod = _FY2023,
) -> FactRead | None:
    return PointInTimeRepository(session).latest_visible_fact(
        company_id=company_id,
        as_of=as_of,
        concept="Revenues",
        unit=unit,
        period=period,
        taxonomy="us-gaap",
        dimensions_hash=_TOTAL,
    )


def test_future_fact_is_invisible(db_session: Session) -> None:
    company = _company("NVDA")
    published = datetime(2024, 2, 1, tzinfo=UTC)
    fact_known_at = datetime(2024, 8, 1, tzinfo=UTC)
    document = _filing(company, "000010", published)
    fact = _fact(company.id, document.id, fact_known_at, "999")
    db_session.add_all([company, document, fact])
    db_session.flush()

    as_of = fact_known_at - timedelta(microseconds=1)
    repo = PointInTimeRepository(db_session)
    assert repo.visible_fact_versions(company_id=company.id, as_of=as_of) == ()
    assert _latest(db_session, company.id, as_of) is None


def test_fact_becomes_visible_at_exact_known_at(db_session: Session) -> None:
    company = _company("NVDA")
    known_at = datetime(2024, 2, 1, 15, 30, tzinfo=UTC)
    document = _filing(company, "000010", known_at)
    fact = _fact(company.id, document.id, known_at, "100")
    db_session.add_all([company, document, fact])
    db_session.flush()

    before = known_at - timedelta(microseconds=1)
    assert _latest(db_session, company.id, before) is None
    visible = _latest(db_session, company.id, known_at)
    assert visible is not None
    assert visible.id == fact.id
    assert visible.value == Decimal(100)


def test_restatement_switches_only_after_the_amendment(db_session: Session) -> None:
    company = _company("NVDA")
    original_at = datetime(2024, 2, 1, tzinfo=UTC)
    amended_at = datetime(2024, 11, 1, tzinfo=UTC)
    original = _filing(company, "000010", original_at)
    amended = _filing(company, "000099", amended_at, "10-K/A")
    first = _fact(company.id, original.id, original_at, "100")
    second = _fact(company.id, amended.id, amended_at, "90")
    db_session.add_all([company, original, amended, first, second])
    db_session.flush()

    between = amended_at - timedelta(seconds=1)
    early = _latest(db_session, company.id, between)
    assert early is not None
    assert early.id == first.id
    assert early.value == Decimal(100)

    repo = PointInTimeRepository(db_session)
    later = repo.visible_fact_versions(company_id=company.id, as_of=amended_at)
    latest = _latest(db_session, company.id, amended_at)
    assert [row.id for row in later] == [first.id, second.id]
    assert latest is not None
    assert latest.id == second.id
    assert latest.value == Decimal(90)


def test_fact_waits_for_its_source_document(db_session: Session) -> None:
    company = _company("NVDA")
    fact_known_at = datetime(2024, 3, 1, tzinfo=UTC)
    document_known_at = datetime(2024, 5, 1, tzinfo=UTC)
    document = _filing(company, "000010", document_known_at)
    fact = _fact(company.id, document.id, fact_known_at, "100")
    db_session.add_all([company, document, fact])
    db_session.flush()

    assert _latest(db_session, company.id, fact_known_at) is None
    visible = _latest(db_session, company.id, document_known_at)
    assert visible is not None
    assert visible.document_id == document.id
    assert visible.value == Decimal(100)


def test_facts_do_not_cross_companies(db_session: Session) -> None:
    acme = _company("ACME")
    other = _company("OTHER")
    known_at = datetime(2024, 2, 1, tzinfo=UTC)
    acme_doc = _filing(acme, "000010", known_at)
    other_doc = _filing(other, "000010", known_at)
    acme_fact = _fact(acme.id, acme_doc.id, known_at, "100")
    other_fact = _fact(other.id, other_doc.id, known_at, "999")
    db_session.add_all([acme, other, acme_doc, other_doc, acme_fact, other_fact])
    db_session.flush()

    acme_latest = _latest(db_session, acme.id, known_at)
    other_latest = _latest(db_session, other.id, known_at)
    assert acme_latest is not None
    assert acme_latest.id == acme_fact.id
    assert acme_latest.value == Decimal(100)
    assert other_latest is not None
    assert other_latest.id == other_fact.id
    assert other_latest.value == Decimal(999)


def test_usd_does_not_match_eur(db_session: Session) -> None:
    company = _company("NVDA")
    known_at = datetime(2024, 2, 1, tzinfo=UTC)
    document = _filing(company, "000010", known_at)
    usd = _fact(company.id, document.id, known_at, "100", unit="USD")
    eur = _fact(company.id, document.id, known_at, "80", unit="EUR")
    db_session.add_all([company, document, usd, eur])
    db_session.flush()

    usd_latest = _latest(db_session, company.id, known_at, unit="USD")
    eur_latest = _latest(db_session, company.id, known_at, unit="EUR")
    assert usd_latest is not None
    assert usd_latest.id == usd.id
    assert usd_latest.value == Decimal(100)
    assert eur_latest is not None
    assert eur_latest.value == Decimal(80)


def test_fy2024_does_not_replace_fy2023(db_session: Session) -> None:
    company = _company("NVDA")
    fy2023_at = datetime(2024, 2, 1, tzinfo=UTC)
    fy2024_at = datetime(2025, 2, 1, tzinfo=UTC)
    first = _filing(company, "000010", fy2023_at)
    second = _filing(company, "000020", fy2024_at)
    fy2023 = _fact(company.id, first.id, fy2023_at, "40")
    fy2024 = _fact(
        company.id,
        second.id,
        fy2024_at,
        "55",
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
    )
    db_session.add_all([company, first, second, fy2023, fy2024])
    db_session.flush()

    prior = _latest(db_session, company.id, fy2024_at, period=_FY2023)
    current = _latest(db_session, company.id, fy2024_at, period=_FY2024)
    assert prior is not None
    assert prior.id == fy2023.id
    assert prior.value == Decimal(40)
    assert current is not None
    assert current.id == fy2024.id
    assert current.value == Decimal(55)


def test_naive_timestamp_is_rejected(db_session: Session) -> None:
    company_id = uuid4()
    naive = datetime(2024, 2, 1)
    with pytest.raises(AsOfError):
        _latest(db_session, company_id, naive)
    with pytest.raises(ValidationError):
        FactCreate(
            company_id=company_id,
            document_id=uuid4(),
            taxonomy="us-gaap",
            concept="Revenues",
            value=Decimal(100),
            unit="USD",
            period_type="duration",
            period_start=date(2023, 1, 1),
            period_end=date(2023, 12, 31),
            known_at=naive,
        )


def test_duplicate_fact_is_rejected(db_session: Session) -> None:
    company = _company("NVDA")
    known_at = datetime(2024, 2, 1, tzinfo=UTC)
    document = _filing(company, "000010", known_at)
    first = _fact(company.id, document.id, known_at, "100")
    duplicate = _fact(company.id, document.id, known_at, "101")
    db_session.add_all([company, document, first, duplicate])
    with pytest.raises(IntegrityError):
        db_session.flush()