"""Job schemas."""

import json
from datetime import UTC, datetime
from typing import Any, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from titan.schemas.base import ApiModel, ReadModel
from titan.schemas.types import JobStatus, UtcDatetime


class JobCreate(ApiModel):
    kind: str = Field(min_length=1)
    status: JobStatus = "queued"
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=1)
    run_after: UtcDatetime = Field(default_factory=lambda: datetime.now(UTC))
    locked_at: UtcDatetime | None = None
    locked_by: str | None = Field(default=None, min_length=1)
    attempts: int = Field(default=0, ge=0)
    last_error: str | None = None

    @field_validator("payload")
    @classmethod
    def _payload_is_json_object(cls, value: dict[str, Any]) -> dict[str, Any]:
        json.dumps(value)
        return value

    @model_validator(mode="after")
    def _locks_are_paired(self) -> Self:
        if (self.locked_at is None) != (self.locked_by is None):
            raise ValueError("locked_at and locked_by must both be set or both be null")
        return self


class JobRead(ReadModel):
    id: UUID
    kind: str
    status: JobStatus
    payload: dict[str, Any]
    idempotency_key: str | None
    run_after: UtcDatetime
    locked_at: UtcDatetime | None
    locked_by: str | None
    attempts: int
    last_error: str | None
    created_at: UtcDatetime
