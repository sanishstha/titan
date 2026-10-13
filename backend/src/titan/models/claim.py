"""Claim stub. Predicate, span, and confidence arrive in a later phase."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Index, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from titan.models.base import Base, CreatedAtMixin, KnownAtMixin, uuid_pk


class Claim(KnownAtMixin, CreatedAtMixin, Base):
    """A reserved claim row. Only identity and ``known_at`` exist in this phase."""

    __tablename__ = "claims"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "company_id"],
            ["documents.id", "documents.company_id"],
            name="fk_claims_document_company",
            ondelete="RESTRICT",
        ),
        Index("ix_claims_company_known_at", "company_id", "known_at"),
        Index("ix_claims_document_id", "document_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("companies.id", name="fk_claims_company_id", ondelete="RESTRICT"),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
