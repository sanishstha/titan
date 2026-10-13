"""Trace schemas."""

from uuid import UUID

from pydantic import Field

from titan.schemas.base import ApiModel, ReadModel
from titan.schemas.types import CostUsd, UtcDatetime


class TraceCreate(ApiModel):
    job_id: UUID | None = None
    model: str = Field(min_length=1)
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    cost_usd: CostUsd
    cache_hit: bool


class TraceRead(ReadModel):
    id: UUID
    job_id: UUID | None
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost_usd: CostUsd
    cache_hit: bool
    created_at: UtcDatetime
