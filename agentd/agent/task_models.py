"""ORM model for session_tasks table — Phase P3 long-run workbench.

Each row represents a long-running task instance (detached process or
blocking child task) spawned within a session. Heavy content (stdout,
stderr, artifacts) lives on the filesystem; this table is the lightweight
index for queries, status tracking, and panel display.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base


class SessionTask(Base):
    __tablename__ = "session_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    spawned_by_tool: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="",
    )
    tool_call_id: Mapped[str] = mapped_column(
        String(128), nullable=False, server_default="",
    )
    task_kind: Mapped[str] = mapped_column(
        String(32), nullable=False,
        doc="process | child_session",
    )
    blocking_mode: Mapped[str] = mapped_column(
        String(16), nullable=False,
        doc="detached | blocking",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued",
        doc="queued | running | waiting | completed | failed | cancelled",
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    command: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    child_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    pid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    stdout_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stderr_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    artifact_root: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_ref: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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
