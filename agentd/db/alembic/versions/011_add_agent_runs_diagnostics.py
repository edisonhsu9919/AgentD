"""Add diagnostics JSONB column to agent_runs table (Phase L).

Stores per-run runtime diagnostics: prompt layer sizes, message counts,
task plan injection state, total prompt tokens, etc.  Enables post-hoc
analysis of prompt continuity and context drop diagnosis.

Revision ID: 011
Revises: 010
Create Date: 2026-03-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column(
            "diagnostics",
            JSONB,
            nullable=True,
            comment="Runtime diagnostics: prompt layer sizes, message counts, plan state, etc.",
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "diagnostics")
