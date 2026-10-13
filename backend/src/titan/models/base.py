"""Declarative base, surrogate keys, and timezone-aware timestamps."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, Uuid, event, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, MappedColumn, mapped_column

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Shared metadata for the Phase 1 financial evidence schema."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


@event.listens_for(Base, "init", propagate=True)
def _assign_uuid(_target: object, _args: tuple[object, ...], kwargs: dict[str, Any]) -> None:
    """Give every row an id before flush so foreign keys can be set immediately."""
    if kwargs.get("id") is None:
        kwargs["id"] = uuid.uuid4()


def uuid_pk() -> MappedColumn[Any]:
    """Surrogate primary key. Public identity lives on natural keys."""
    return mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )


class KnownAtMixin:
    """When this row became public. Reads keep rows with ``known_at <= as_of``."""

    known_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CreatedAtMixin:
    """Insertion time for operational rows. This is not a visibility cutoff."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
