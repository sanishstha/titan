"""A public company. CIK is the stable key; ticker is only a label."""

from __future__ import annotations

import uuid

from sqlalchemy import CHAR, CheckConstraint, Index, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from titan.models.base import Base, uuid_pk


class Company(Base):
    """One SEC registrant, identified by a zero-padded 10-digit CIK."""

    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("cik", name="uq_companies_cik"),
        CheckConstraint("cik ~ '^[0-9]{10}$'", name="cik_digits"),
        CheckConstraint("char_length(btrim(name)) > 0", name="name_nonempty"),
        CheckConstraint(
            "ticker IS NULL OR char_length(btrim(ticker)) > 0",
            name="ticker_nonempty",
        ),
        Index("ix_companies_ticker", "ticker"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    cik: Mapped[str] = mapped_column(CHAR(10), nullable=False)
    ticker: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
