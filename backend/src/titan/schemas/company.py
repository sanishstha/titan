"""Company schemas."""

from uuid import UUID

from pydantic import Field

from titan.schemas.base import ApiModel, ReadModel
from titan.schemas.types import Cik


class CompanyCreate(ApiModel):
    cik: Cik
    ticker: str | None = Field(default=None, min_length=1)
    name: str = Field(min_length=1)


class CompanyRead(ReadModel):
    id: UUID
    cik: Cik
    ticker: str | None
    name: str
