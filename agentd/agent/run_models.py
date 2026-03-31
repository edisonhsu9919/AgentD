"""ORM model for agent_runs table — Phase C scheduler/worker backbone.

Each row represents a discrete unit of work: start a new agent loop,
resume after permission approval, or abort a running session.
Workers claim runs from this table using SELECT ... FOR UPDATE SKIP LOCKED.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_type: Mapped[str] = mapped_column(
        String(16), nullable=False,
        doc="start | resume | abort",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued",
        doc="queued | claimed | running | completed | failed | cancelled",
    )
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict,
        doc="Run-type-specific data: start={user_message, agent_id, model_id, ...}, resume={decisions:[...]}, abort={}",
    )
    worker_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True,
        doc="Identifier of the worker that claimed this run",
    )
    diagnostics: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True,
        doc="Runtime diagnostics: prompt layer sizes, message counts, plan state, total prompt tokens",
    )
    error: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        doc="Error message if status=failed",
    )
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        doc="Worker must finish or renew before this time; null = no active lease",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
