"""One traced model call. Prompt text is intentionally absent in this phase."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from titan.models.base import Base, CreatedAtMixin, uuid_pk


class Trace(CreatedAtMixin, Base):
    """Tokens, latency, and cost for a single model call."""

    __tablename__ = "traces"
    __table_args__ = (
        CheckConstraint("char_length(btrim(model)) > 0", name="model_nonempty"),
        CheckConstraint("tokens_in >= 0", name="tokens_in_nonnegative"),
        CheckConstraint("tokens_out >= 0", name="tokens_out_nonnegative"),
        CheckConstraint("latency_ms >= 0", name="latency_ms_nonnegative"),
        CheckConstraint("cost_usd >= 0", name="cost_usd_nonnegative"),
        Index("ix_traces_job_id", "job_id"),
        Index("ix_traces_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jobs.id", name="fk_traces_job_id", ondelete="SET NULL"),
        nullable=True,
    )
    model: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    cache_hit: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
