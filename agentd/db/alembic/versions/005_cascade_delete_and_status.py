"""Add CASCADE delete to permission_requests and tool_calls FK;
widen permission_requests.status to 20 chars for 'resumed' status;
formalize 'queued' as session status with CHECK comment.

Revision ID: 005
Revises: 004
Create Date: 2026-03-17
"""

from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Fix permission_requests.session_id: add CASCADE ──
    op.drop_constraint(
        "permission_requests_session_id_fkey",
        "permission_requests",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "permission_requests_session_id_fkey",
        "permission_requests",
        "sessions",
        ["session_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # ── Fix tool_calls.session_id: add CASCADE ──
    op.drop_constraint(
        "tool_calls_session_id_fkey",
        "tool_calls",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "tool_calls_session_id_fkey",
        "tool_calls",
        "sessions",
        ["session_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # ── Widen permission_requests.status from 16 to 20 chars ──
    # Needed for new 'resumed' / 'auto_approved' statuses
    op.alter_column(
        "permission_requests",
        "status",
        type_=sa.String(20),
        existing_type=sa.String(16),
        existing_nullable=False,
    )

    # ── Formal session status vocabulary ──
    # idle, queued, running, waiting, error
    # No CHECK constraint added (String(16) is sufficient and we use app-level validation)
    # This migration documents the formal status set via comment
    op.execute(
        "COMMENT ON COLUMN sessions.status IS "
        "'Formal status: idle | queued | running | waiting | error'"
    )


def downgrade() -> None:
    op.execute("COMMENT ON COLUMN sessions.status IS NULL")

    op.alter_column(
        "permission_requests",
        "status",
        type_=sa.String(16),
        existing_type=sa.String(20),
        existing_nullable=False,
    )

    # Revert tool_calls FK (remove CASCADE)
    op.drop_constraint(
        "tool_calls_session_id_fkey",
        "tool_calls",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "tool_calls_session_id_fkey",
        "tool_calls",
        "sessions",
        ["session_id"],
        ["id"],
    )

    # Revert permission_requests FK (remove CASCADE)
    op.drop_constraint(
        "permission_requests_session_id_fkey",
        "permission_requests",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "permission_requests_session_id_fkey",
        "permission_requests",
        "sessions",
        ["session_id"],
        ["id"],
    )
