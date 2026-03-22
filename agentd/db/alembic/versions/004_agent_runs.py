"""Add agent_runs table for Phase C scheduler/worker architecture.

Revision ID: 004
Revises: 003
Create Date: 2026-03-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", UUID(as_uuid=True), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("worker_id", sa.String(64), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # Worker poll query: find queued runs ordered by creation time
    op.create_index("idx_agent_runs_status_created", "agent_runs", ["status", "created_at"])
    # Lookup runs by session (for abort checks, history)
    op.create_index("idx_agent_runs_session_id", "agent_runs", ["session_id"])
    # Lease expiry check for stale claim recovery
    op.create_index("idx_agent_runs_lease_expires", "agent_runs", ["lease_expires_at"],
                     postgresql_where=sa.text("status IN ('claimed', 'running')"))

    # Also add 'queued' to the session status vocabulary by updating the
    # sessions table — no schema change needed since status is String(16)


def downgrade() -> None:
    op.drop_index("idx_agent_runs_lease_expires", table_name="agent_runs")
    op.drop_index("idx_agent_runs_session_id", table_name="agent_runs")
    op.drop_index("idx_agent_runs_status_created", table_name="agent_runs")
    op.drop_table("agent_runs")
