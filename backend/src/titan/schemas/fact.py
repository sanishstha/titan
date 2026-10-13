"""Fact schemas. The dimension hash is derived from canonical JSON."""

from datetime import date
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from titan.common.dimensions import hash_dimensions
from titan.schemas.base import ApiModel, ReadModel
from titan.schemas.types import FinancialValue, FiscalPeriod, PeriodType, UtcDatetime


class FactCreate(ApiModel):
    company_id: UUID
    document_id: UUID
    taxonomy: str = Field(min_length=1)
    concept: str = Field(min_length=1)
    value: FinancialValue
    unit: str = Field(min_length=1)
    decimals: int | None = None
    period_type: PeriodType
    period_start: date | None = None
    period_end: date
    fiscal_year: int | None = Field(default=None, ge=1900, le=2200)
    fiscal_period: FiscalPeriod | None = None
    known_at: UtcDatetime
    dimensions: dict[str, str] = Field(default_factory=dict)
    dimensions_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _check_period_and_hash(self) -> Self:
        if self.period_type == "instant":
            if self.period_start is not None:
                raise ValueError("instant facts must omit period_start")
        elif self.period_start is None or self.period_start > self.period_end:
            raise ValueError("duration facts require period_start <= period_end")
        expected = hash_dimensions(self.dimensions)
        if self.dimensions_hash is not None and self.dimensions_hash != expected:
            raise ValueError("dimensions_hash does not match canonical dimensions JSON")
        self.dimensions_hash = expected
        return self


class FactRead(ReadModel):
    id: UUID
    company_id: UUID
    document_id: UUID
    taxonomy: str
    concept: str
    value: FinancialValue
    unit: str
    decimals: int | None
    period_type: PeriodType
    period_start: date | None
    period_end: date
    fiscal_year: int | None
    fiscal_period: FiscalPeriod | None
    known_at: UtcDatetime
    dimensions: dict[str, str]
    dimensions_hash: str
