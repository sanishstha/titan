"""Pydantic bases. Write models reject unknown fields. Read models are frozen."""

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    """Input model. Unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")


class ReadModel(BaseModel):
    """Output model. Frozen, and loadable from an ORM object."""

    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)
