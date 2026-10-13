"""Schema validation for companies, filings, facts, jobs, and traces."""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from titan.common.dimensions import hash_dimensions
from titan.schemas.company import CompanyCreate, CompanyRead
from titan.schemas.document import DocumentCreate
from titan.schemas.fact import FactCreate
from titan.schemas.job import JobCreate
from titan.schemas.trace import TraceCreate

_EMPTY_HASH = "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
_KNOWN_AT = datetime(2024, 2, 15, 21, 30, tzinfo=UTC)


def _fact_kwargs() -> dict[str, object]:
    return {
        "company_id": uuid4(),
        "document_id": uuid4(),
        "taxonomy": "us-gaap",
        "concept": "Assets",
        "value": Decimal("100.50"),
        "unit": "USD",
        "period_type": "instant",
        "period_end": date(2023, 12, 31),
        "known_at": _KNOWN_AT,
    }


def test_cik_and_accession_patterns() -> None:
    CompanyCreate(cik="0000320193", ticker="AAPL", name="Apple Inc.")
    with pytest.raises(ValidationError):
        CompanyCreate(cik="320193", ticker="AAPL", name="Apple Inc.")
    DocumentCreate(
        company_id=uuid4(),
        accession_number="0000320193-24-000010",
        form_type="10-K",
        filed_at=_KNOWN_AT,
        known_at=_KNOWN_AT,
    )
    with pytest.raises(ValidationError):
        DocumentCreate(
            company_id=uuid4(),
            accession_number="000032019324000010",
            form_type="10-K",
            filed_at=_KNOWN_AT,
            known_at=_KNOWN_AT,
        )


def test_naive_datetime_is_rejected_and_offsets_become_utc() -> None:
    with pytest.raises(ValidationError):
        DocumentCreate(
            company_id=uuid4(),
            accession_number="0000320193-24-000010",
            form_type="10-K",
            filed_at=datetime(2024, 2, 15, 16, 30),
            known_at=_KNOWN_AT,
        )
    eastern = timezone(timedelta(hours=-5))
    document = DocumentCreate(
        company_id=uuid4(),
        accession_number="0000320193-24-000010",
        form_type="10-K",
        filed_at=datetime(2024, 2, 15, 16, 30, tzinfo=eastern),
        known_at=_KNOWN_AT,
    )
    assert document.filed_at == datetime(2024, 2, 15, 21, 30, tzinfo=UTC)


def test_unknown_fields_and_frozen_reads() -> None:
    with pytest.raises(ValidationError):
        CompanyCreate(cik="0000320193", ticker="AAPL", name="Apple Inc.", extra="no")
    company = CompanyRead(id=uuid4(), cik="0000320193", ticker="AAPL", name="Apple Inc.")
    with pytest.raises(ValidationError):
        company.name = "Other"  # type: ignore[misc]


def test_fact_value_rejects_float_and_keeps_decimal() -> None:
    fact = FactCreate(**_fact_kwargs())
    assert fact.value == Decimal("100.50")
    assert fact.dimensions_hash == _EMPTY_HASH
    kwargs = _fact_kwargs()
    kwargs["value"] = 0.1
    with pytest.raises(ValidationError):
        FactCreate(**kwargs)


def test_duration_and_instant_period_rules() -> None:
    instant = _fact_kwargs()
    instant["period_start"] = date(2023, 1, 1)
    with pytest.raises(ValidationError):
        FactCreate(**instant)

    duration = _fact_kwargs()
    duration["period_type"] = "duration"
    duration["concept"] = "Revenues"
    with pytest.raises(ValidationError):
        FactCreate(**duration)

    duration["period_start"] = date(2024, 1, 1)
    with pytest.raises(ValidationError):
        FactCreate(**duration)

    duration["period_start"] = date(2023, 1, 1)
    created = FactCreate(**duration)
    assert created.period_start == date(2023, 1, 1)


def test_dimensions_hash_is_canonical_and_must_match() -> None:
    kwargs = _fact_kwargs()
    kwargs["dimensions"] = {"b": "MemberB", "a": "MemberA"}
    fact = FactCreate(**kwargs)
    assert fact.dimensions_hash == hash_dimensions({"a": "MemberA", "b": "MemberB"})
    kwargs["dimensions_hash"] = _EMPTY_HASH
    with pytest.raises(ValidationError):
        FactCreate(**kwargs)


def test_trace_cost_rejects_float_and_negative() -> None:
    TraceCreate(
        model="mock",
        tokens_in=1,
        tokens_out=2,
        latency_ms=3,
        cost_usd=Decimal("0.00000001"),
        cache_hit=False,
    )
    with pytest.raises(ValidationError):
        TraceCreate(
            model="mock",
            tokens_in=1,
            tokens_out=2,
            latency_ms=3,
            cost_usd=0.1,
            cache_hit=False,
        )


def test_job_locks_must_be_paired() -> None:
    with pytest.raises(ValidationError):
        JobCreate(kind="ingest", locked_by="worker-1")
    job = JobCreate(kind="ingest", locked_at=_KNOWN_AT, locked_by="worker-1")
    assert job.status == "queued"
    assert job.payload == {}
