"""014: Add session_tasks table for long-running task instances (Phase P3).

Lightweight index table for detached process jobs and blocking child tasks.
Heavy content (stdout/stderr/artifacts) lives on the filesystem at
.agentd/tasks/{task_id}/.
"""

import sqlalchemy as sa
from alembic import op


revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "session_tasks",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("spawned_by_tool", sa.String(64), nullable=False, server_default=""),
        sa.Column("tool_call_id", sa.String(128), nullable=False, server_default=""),
        sa.Column(
            "task_kind",
            sa.String(32),
            nullable=False,
            doc="process | child_session",
        ),
        sa.Column(
            "blocking_mode",
            sa.String(16),
            nullable=False,
            doc="detached | blocking",
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="queued",
            doc="queued | running | waiting | completed | failed | cancelled",
        ),
        sa.Column("title", sa.Text, nullable=False, server_default=""),
        sa.Column("command", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "child_session_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("pid", sa.Integer, nullable=True),
        sa.Column("stdout_path", sa.Text, nullable=True),
        sa.Column("stderr_path", sa.Text, nullable=True),
        sa.Column("artifact_root", sa.Text, nullable=True),
        sa.Column("result_ref", sa.Text, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_session_tasks_session_id", "session_tasks", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_session_tasks_session_id")
    op.drop_table("session_tasks")
