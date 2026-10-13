"""Leakage tests for the point-in-time repository.

Storage tests may still read tables to prove rows were kept. Application reads
of documents and facts go through ``PointInTimeRepository``.
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from titan.common.dimensions import hash_dimensions
from titan.common.session import create_db_engine, create_session_factory
from titan.models import Company, Document, Fact
from titan.pit.repository import (
    AsOfError,
    PointInTimeRepository,
    ReportingPeriod,
    _latest_visible_fact_statement,
    _visible_documents_statement,
    _visible_fact_versions_statement,
)
from titan.schemas.document import DocumentRead
from titan.schemas.fact import FactRead

_TOTAL = hash_dimensions({})
_SEGMENT = hash_dimensions({"SegmentAxis": "Hardware"})
_INSTANT = ReportingPeriod(period_end=date(2023, 12, 31))


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


def _company() -> Company:
    return Company(cik=f"{uuid4().int % 10**10:010d}", ticker="TEST", name="Fixture Co")


def _document(
    company_id: UUID,
    accession_number: str,
    known_at: datetime,
    form_type: str = "10-K",
    filed_at: datetime | None = None,
    period_of_report: date | None = date(2023, 12, 31),
) -> Document:
    return Document(
        company_id=company_id,
        accession_number=accession_number,
        form_type=form_type,
        filed_at=known_at if filed_at is None else filed_at,
        known_at=known_at,
        period_of_report=period_of_report,
    )


def _fact(
    company_id: UUID,
    document_id: UUID,
    known_at: datetime,
    value: str,
    *,
    concept: str = "Assets",
    unit: str = "USD",
    taxonomy: str = "us-gaap",
    period_start: date | None = None,
    period_end: date = date(2023, 12, 31),
    dimensions: dict[str, str] | None = None,
) -> Fact:
    stored = {} if dimensions is None else dimensions
    return Fact(
        company_id=company_id,
        document_id=document_id,
        taxonomy=taxonomy,
        concept=concept,
        value=Decimal(value),
        unit=unit,
        period_type="instant" if period_start is None else "duration",
        period_start=period_start,
        period_end=period_end,
        known_at=known_at,
        dimensions=stored,
        dimensions_hash=hash_dimensions(stored),
    )


def _accession(cik: str, suffix: str) -> str:
    return f"{cik}-24-{suffix}"


def _repo(session: Session) -> PointInTimeRepository:
    return PointInTimeRepository(session)


def _latest(
    repo: PointInTimeRepository,
    company_id: UUID,
    as_of: datetime,
    *,
    concept: str = "Assets",
    unit: str = "USD",
    period: ReportingPeriod = _INSTANT,
    taxonomy: str = "us-gaap",
    dimensions_hash: str = _TOTAL,
) -> FactRead | None:
    return repo.latest_visible_fact(
        company_id=company_id,
        as_of=as_of,
        concept=concept,
        unit=unit,
        period=period,
        taxonomy=taxonomy,
        dimensions_hash=dimensions_hash,
    )


def test_repository_exposes_read_methods_only() -> None:
    methods = {
        name
        for name, value in vars(PointInTimeRepository).items()
        if callable(value) and not name.startswith("_")
    }
    assert methods == {
        "latest_visible_fact",
        "visible_documents",
        "visible_fact_versions",
    }


def test_naive_as_of_is_rejected() -> None:
    with Session() as session:
        repo = PointInTimeRepository(session)
        naive = datetime(2024, 1, 1)
        company_id = uuid4()
        with pytest.raises(AsOfError):
            repo.visible_documents(company_id=company_id, as_of=naive)
        with pytest.raises(AsOfError):
            repo.visible_fact_versions(company_id=company_id, as_of=naive)
        with pytest.raises(AsOfError):
            _latest(repo, company_id, naive)


def test_latest_fact_rejects_a_blank_or_malformed_identity() -> None:
    with Session() as session:
        repo = PointInTimeRepository(session)
        as_of = datetime(2024, 6, 1, tzinfo=UTC)
        company_id = uuid4()
        with pytest.raises(ValueError, match="concept"):
            _latest(repo, company_id, as_of, concept=" ")
        with pytest.raises(ValueError, match="dimensions_hash"):
            _latest(repo, company_id, as_of, dimensions_hash="ABC")


def test_reporting_period_rejects_a_start_after_the_end() -> None:
    with pytest.raises(ValueError, match="period_start"):
        ReportingPeriod(period_end=date(2023, 1, 1), period_start=date(2023, 2, 1))


def test_compiled_sql_applies_the_cutoff_to_facts_and_source_documents() -> None:
    company_id = uuid4()
    as_of = datetime(2024, 6, 1, tzinfo=UTC)
    documents = _visible_documents_statement(company_id, as_of)
    versions = _visible_fact_versions_statement(company_id, as_of)
    latest = _latest_visible_fact_statement(
        company_id=company_id,
        as_of=as_of,
        concept="Assets",
        unit="USD",
        period=_INSTANT,
        taxonomy="us-gaap",
        dimensions_hash=_TOTAL,
    )
    document_sql = _sql(documents)
    version_sql = _sql(versions)
    latest_sql = _sql(latest)

    assert "documents.known_at <=" in document_sql
    assert "DISTINCT ON" not in document_sql
    assert "facts.known_at <=" in version_sql
    assert "documents.known_at <=" in version_sql
    assert "JOIN documents" in version_sql
    assert "DISTINCT ON" not in version_sql
    assert "facts.known_at ASC" in version_sql
    assert "DISTINCT ON" in latest_sql
    assert "facts.known_at <=" in latest_sql
    assert "documents.known_at <=" in latest_sql
    assert "facts.known_at DESC" in latest_sql
    assert 'documents.accession_number COLLATE "C" DESC' in latest_sql
    assert 'facts.unit COLLATE "C"' in latest_sql
    assert "facts.period_start IS NULL" in latest_sql


def _sql(statement: Select[Any]) -> str:
    compiled = statement.compile(dialect=postgresql.dialect())
    return " ".join(str(compiled).split())


def test_boundary_includes_the_cutoff_and_ignores_filed_at(
    db_session: Session,
) -> None:
    company = _company()
    known_at = datetime(2024, 2, 1, 15, 30, tzinfo=UTC)
    future_filing_clock = datetime(2026, 1, 1, tzinfo=UTC)
    document = _document(
        company.id,
        _accession(company.cik, "000010"),
        known_at,
        filed_at=future_filing_clock,
        period_of_report=date(2025, 12, 31),
    )
    fact = _fact(company.id, document.id, known_at, "100.10")
    hidden = _document(
        company.id,
        _accession(company.cik, "000099"),
        known_at + timedelta(days=1),
        filed_at=datetime(2020, 1, 1, tzinfo=UTC),
        form_type="10-K/A",
    )
    hidden_fact = _fact(company.id, hidden.id, hidden.known_at, "90.25")
    db_session.add_all([company, document, hidden, fact, hidden_fact])
    db_session.flush()
    repo = _repo(db_session)

    before = known_at - timedelta(microseconds=1)
    assert repo.visible_documents(company_id=company.id, as_of=before) == ()
    assert repo.visible_fact_versions(company_id=company.id, as_of=before) == ()
    assert _latest(repo, company.id, before) is None

    documents = repo.visible_documents(company_id=company.id, as_of=known_at)
    versions = repo.visible_fact_versions(company_id=company.id, as_of=known_at)
    latest = _latest(repo, company.id, known_at)
    assert [item.accession_number for item in documents] == [document.accession_number]
    assert [item.value for item in versions] == [Decimal("100.10")]
    assert latest is not None
    assert latest.value == Decimal("100.10")
    assert latest.document_id == document.id


def test_future_rows_and_other_companies_stay_hidden(db_session: Session) -> None:
    company = _company()
    other = _company()
    as_of = datetime(2024, 6, 1, tzinfo=UTC)
    original = _document(company.id, _accession(company.cik, "000010"), as_of)
    future = _document(
        company.id,
        _accession(company.cik, "000020"),
        as_of + timedelta(seconds=1),
        form_type="10-Q",
    )
    outsider = _document(other.id, _accession(other.cik, "000010"), as_of - timedelta(days=1))
    original_fact = _fact(company.id, original.id, original.known_at, "10")
    future_fact = _fact(company.id, future.id, future.known_at, "999")
    outsider_fact = _fact(other.id, outsider.id, outsider.known_at, "888")
    db_session.add_all(
        [
            company,
            other,
            original,
            future,
            outsider,
            original_fact,
            future_fact,
            outsider_fact,
        ]
    )
    db_session.flush()
    repo = _repo(db_session)

    documents = repo.visible_documents(company_id=company.id, as_of=as_of)
    versions = repo.visible_fact_versions(company_id=company.id, as_of=as_of)
    latest = _latest(repo, company.id, as_of)
    assert [item.id for item in documents] == [original.id]
    assert [item.value for item in versions] == [Decimal(10)]
    assert latest is not None
    assert latest.value == Decimal(10)
    assert _latest(repo, other.id, as_of - timedelta(days=2)) is None


def test_restatement_preserves_the_original_and_as_of_selects_the_known_value(
    db_session: Session,
) -> None:
    company = _company()
    original_at = datetime(2024, 2, 1, tzinfo=UTC)
    restated_at = datetime(2024, 11, 1, tzinfo=UTC)
    original = _document(company.id, _accession(company.cik, "000010"), original_at)
    amended = _document(
        company.id,
        _accession(company.cik, "000099"),
        restated_at,
        form_type="10-K/A",
    )
    first = _fact(company.id, original.id, original_at, "100.10")
    second = _fact(company.id, amended.id, restated_at, "90.25")
    db_session.add_all([company, original, amended, first, second])
    db_session.flush()
    repo = _repo(db_session)

    between = restated_at - timedelta(seconds=1)
    early_versions = repo.visible_fact_versions(company_id=company.id, as_of=between)
    early_latest = _latest(repo, company.id, between)
    assert [item.value for item in early_versions] == [Decimal("100.10")]
    assert early_latest is not None
    assert early_latest.id == first.id
    early_documents = repo.visible_documents(company_id=company.id, as_of=between)
    assert [item.form_type for item in early_documents] == ["10-K"]

    later_versions = repo.visible_fact_versions(company_id=company.id, as_of=restated_at)
    later_latest = _latest(repo, company.id, restated_at)
    assert [item.value for item in later_versions] == [Decimal("100.10"), Decimal("90.25")]
    assert [item.id for item in later_versions] == [first.id, second.id]
    assert later_latest is not None
    assert later_latest.id == second.id
    assert later_latest.value == Decimal("90.25")
    assert later_versions[-1] == later_latest


def test_fact_stays_hidden_until_its_source_document_is_visible(
    db_session: Session,
) -> None:
    company = _company()
    fact_known_at = datetime(2024, 3, 1, tzinfo=UTC)
    document_known_at = datetime(2024, 5, 1, tzinfo=UTC)
    document = _document(company.id, _accession(company.cik, "000010"), document_known_at)
    fact = _fact(company.id, document.id, fact_known_at, "777")
    db_session.add_all([company, document, fact])
    db_session.flush()
    repo = _repo(db_session)

    too_early = document_known_at - timedelta(microseconds=1)
    assert repo.visible_documents(company_id=company.id, as_of=fact_known_at) == ()
    assert repo.visible_fact_versions(company_id=company.id, as_of=too_early) == ()
    assert _latest(repo, company.id, too_early) is None

    visible = repo.visible_fact_versions(company_id=company.id, as_of=document_known_at)
    latest = _latest(repo, company.id, document_known_at)
    assert [item.value for item in visible] == [Decimal(777)]
    assert latest is not None
    assert latest.value == Decimal(777)


def test_future_fact_on_an_already_public_document_stays_hidden(
    db_session: Session,
) -> None:
    company = _company()
    document_known_at = datetime(2024, 2, 1, tzinfo=UTC)
    fact_known_at = datetime(2024, 8, 1, tzinfo=UTC)
    document = _document(company.id, _accession(company.cik, "000010"), document_known_at)
    fact = _fact(company.id, document.id, fact_known_at, "888")
    db_session.add_all([company, document, fact])
    db_session.flush()
    repo = _repo(db_session)
    between = fact_known_at - timedelta(seconds=1)

    documents = repo.visible_documents(company_id=company.id, as_of=between)
    assert [item.id for item in documents] == [document.id]
    assert repo.visible_fact_versions(company_id=company.id, as_of=between) == ()
    assert _latest(repo, company.id, between) is None
    revealed = _latest(repo, company.id, fact_known_at)
    assert revealed is not None
    assert revealed.value == Decimal(888)


def test_latest_fact_uses_the_explicit_concept_unit_and_period(
    db_session: Session,
) -> None:
    company = _company()
    known_at = datetime(2024, 2, 1, tzinfo=UTC)
    document = _document(company.id, _accession(company.cik, "000010"), known_at)
    assets = _fact(company.id, document.id, known_at, "100")
    shares = _fact(company.id, document.id, known_at, "5", unit="shares")
    revenue = _fact(
        company.id,
        document.id,
        known_at,
        "40",
        concept="Revenues",
        period_start=date(2023, 1, 1),
    )
    prior_assets = _fact(
        company.id,
        document.id,
        known_at,
        "80",
        period_end=date(2022, 12, 31),
    )
    segment = _fact(company.id, document.id, known_at, "30", dimensions={"SegmentAxis": "Hardware"})
    ifrs = _fact(company.id, document.id, known_at, "70", taxonomy="ifrs-full")
    db_session.add_all(
        [company, document, assets, shares, revenue, prior_assets, segment, ifrs]
    )
    db_session.flush()
    repo = _repo(db_session)

    selected = _latest(repo, company.id, known_at)
    assert selected is not None
    assert selected.id == assets.id
    shares_latest = _latest(repo, company.id, known_at, unit="shares")
    assert shares_latest is not None
    assert shares_latest.value == Decimal(5)
    year = ReportingPeriod(period_end=date(2023, 12, 31), period_start=date(2023, 1, 1))
    revenue_latest = _latest(repo, company.id, known_at, concept="Revenues", period=year)
    assert revenue_latest is not None
    assert revenue_latest.value == Decimal(40)
    prior = _latest(
        repo,
        company.id,
        known_at,
        period=ReportingPeriod(period_end=date(2022, 12, 31)),
    )
    assert prior is not None
    assert prior.value == Decimal(80)
    segment_latest = _latest(repo, company.id, known_at, dimensions_hash=_SEGMENT)
    assert segment_latest is not None
    assert segment_latest.value == Decimal(30)
    ifrs_latest = _latest(repo, company.id, known_at, taxonomy="ifrs-full")
    assert ifrs_latest is not None
    assert ifrs_latest.value == Decimal(70)
    assert _latest(repo, company.id, known_at, concept="Liabilities") is None


def test_versions_are_ordered_and_repeatable(db_session: Session) -> None:
    company = _company()
    early = datetime(2024, 2, 1, tzinfo=UTC)
    later = datetime(2024, 8, 1, tzinfo=UTC)
    first = _document(company.id, _accession(company.cik, "000030"), later, form_type="10-Q")
    second = _document(company.id, _accession(company.cik, "000010"), early)
    duration = _fact(
        company.id,
        second.id,
        early,
        "40",
        period_start=date(2023, 1, 1),
    )
    instant_later = _fact(company.id, first.id, later, "12")
    instant_early = _fact(company.id, second.id, early, "11")
    shares = _fact(company.id, second.id, early, "7", unit="shares")
    db_session.add_all(
        [company, first, second, shares, instant_later, instant_early, duration]
    )
    db_session.flush()
    repo = _repo(db_session)

    documents = repo.visible_documents(company_id=company.id, as_of=later)
    versions = repo.visible_fact_versions(company_id=company.id, as_of=later)
    assert [item.accession_number for item in documents] == [
        second.accession_number,
        first.accession_number,
    ]
    assert [item.id for item in versions] == [
        duration.id,
        instant_early.id,
        instant_later.id,
        shares.id,
    ]
    assert documents == repo.visible_documents(company_id=company.id, as_of=later)
    assert versions == repo.visible_fact_versions(company_id=company.id, as_of=later)


def test_shared_known_at_prefers_the_later_accession_number(db_session: Session) -> None:
    company = _company()
    known_at = datetime(2024, 2, 1, 12, tzinfo=UTC)
    lower = _document(company.id, _accession(company.cik, "000001"), known_at)
    higher = _document(
        company.id,
        _accession(company.cik, "000002"),
        known_at,
        form_type="10-K/A",
    )
    lower_fact = _fact(company.id, lower.id, known_at, "2")
    higher_fact = _fact(company.id, higher.id, known_at, "1")
    db_session.add_all([company, higher, lower, higher_fact, lower_fact])
    db_session.flush()
    repo = _repo(db_session)

    versions = repo.visible_fact_versions(company_id=company.id, as_of=known_at)
    latest = _latest(repo, company.id, known_at)
    assert [item.id for item in versions] == [lower_fact.id, higher_fact.id]
    assert latest is not None
    assert latest.id == higher_fact.id
    assert latest.value == Decimal(1)
    assert versions[-1] == latest


def test_non_utc_as_of_matches_the_same_instant(db_session: Session) -> None:
    company = _company()
    known_at = datetime(2024, 2, 1, tzinfo=UTC)
    document = _document(company.id, _accession(company.cik, "000010"), known_at)
    fact = _fact(company.id, document.id, known_at, "100.10")
    db_session.add_all([company, document, fact])
    db_session.flush()
    repo = _repo(db_session)
    plus_11 = timezone(timedelta(hours=11))
    same_instant = known_at.astimezone(plus_11)
    one_minute_earlier = same_instant - timedelta(minutes=1)

    assert repo.visible_fact_versions(company_id=company.id, as_of=one_minute_earlier) == ()
    found = repo.visible_documents(company_id=company.id, as_of=same_instant)
    assert [item.id for item in found] == [document.id]
    latest = _latest(repo, company.id, same_instant)
    assert latest is not None
    assert latest.known_at == known_at


def test_read_models_are_snapshots(db_session: Session) -> None:
    company = _company()
    known_at = datetime(2024, 2, 1, tzinfo=UTC)
    document = _document(company.id, _accession(company.cik, "000010"), known_at)
    fact = _fact(company.id, document.id, known_at, "100.10")
    db_session.add_all([company, document, fact])
    db_session.flush()
    repo = _repo(db_session)
    loaded = _latest(repo, company.id, known_at)
    documents = repo.visible_documents(company_id=company.id, as_of=known_at)
    assert isinstance(loaded, FactRead)
    assert isinstance(documents[0], DocumentRead)
    assert loaded is not None
    with pytest.raises(ValueError):
        loaded.value = Decimal(1)  # type: ignore[misc]
