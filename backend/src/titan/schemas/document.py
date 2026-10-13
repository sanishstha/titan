"""Document schemas."""

from datetime import date
from uuid import UUID

from pydantic import Field

from titan.schemas.base import ApiModel, ReadModel
from titan.schemas.types import AccessionNumber, UtcDatetime


class DocumentCreate(ApiModel):
    company_id: UUID
    accession_number: AccessionNumber
    form_type: str = Field(min_length=1)
    filed_at: UtcDatetime
    known_at: UtcDatetime
    period_of_report: date | None = None


class DocumentRead(ReadModel):
    id: UUID
    company_id: UUID
    accession_number: AccessionNumber
    form_type: str
    filed_at: UtcDatetime
    known_at: UtcDatetime
    period_of_report: date | None
