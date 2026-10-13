"""Shared schema types. Timestamps must be timezone-aware and are stored in UTC."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BeforeValidator, Field

Cik = Annotated[str, Field(pattern=r"^[0-9]{10}$")]
AccessionNumber = Annotated[str, Field(pattern=r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")]
PeriodType = Literal["duration", "instant"]
FiscalPeriod = Literal["FY", "Q1", "Q2", "Q3", "Q4"]
JobStatus = Literal["queued", "running", "succeeded", "failed"]


def _reject_binary_float(value: object) -> object:
    if isinstance(value, (bool, float)):
        # ValueError is what Pydantic turns into a validation error.
        raise ValueError("use Decimal or a numeric string, not float or bool")  # noqa: TRY004
    return value


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_as_utc)]

__all__ = [
    "AccessionNumber",
    "AwareDatetime",
    "Cik",
    "CostUsd",
    "FinancialValue",
    "FiscalPeriod",
    "JobStatus",
    "PeriodType",
    "UtcDatetime",
]
FinancialValue = Annotated[
    Decimal,
    BeforeValidator(_reject_binary_float),
    Field(max_digits=38, decimal_places=10),
]
CostUsd = Annotated[
    Decimal,
    BeforeValidator(_reject_binary_float),
    Field(max_digits=18, decimal_places=8, ge=0),
]
