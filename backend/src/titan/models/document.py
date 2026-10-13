"""Filing metadata. Accession number is the SEC submission identity."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from titan.models.base import Base, KnownAtMixin, uuid_pk


class Document(KnownAtMixin, Base):
    """One SEC submission, including an amendment.

    ``(id, company_id)`` is unique so facts and claims can use a composite
    foreign key and cannot point at another company's filing.
    ``known_at`` is the acceptance time. ``filed_at`` is metadata only.
    """

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("id", "company_id", name="uq_documents_id_company"),
        UniqueConstraint("accession_number", name="uq_documents_accession_number"),
        CheckConstraint(
            "accession_number ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'",
            name="accession_number_format",
        ),
        CheckConstraint(
            "char_length(btrim(form_type)) > 0",
            name="form_type_nonempty",
        ),
        Index("ix_documents_company_known_at", "company_id", "known_at"),
        Index(
            "ix_documents_company_form_period",
            "company_id",
            "form_type",
            "period_of_report",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("companies.id", name="fk_documents_company_id", ondelete="RESTRICT"),
        nullable=False,
    )
    accession_number: Mapped[str] = mapped_column(Text, nullable=False)
    form_type: Mapped[str] = mapped_column(Text, nullable=False)
    filed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_of_report: Mapped[date | None] = mapped_column(Date, nullable=True)
