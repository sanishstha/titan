"""Claim schemas. This phase stores identity and ``known_at`` only."""

from uuid import UUID

from titan.schemas.base import ApiModel, ReadModel
from titan.schemas.types import UtcDatetime


class ClaimCreate(ApiModel):
    company_id: UUID
    document_id: UUID | None = None
    known_at: UtcDatetime


class ClaimRead(ReadModel):
    id: UUID
    company_id: UUID
    document_id: UUID | None
    known_at: UtcDatetime
    created_at: UtcDatetime
