"""Add context_window column to model_configs table (Phase L §12.5).

Stores the model's context window size in tokens. Used for:
- Computing context usage ratio in run diagnostics
- Future automatic compaction trigger (distance to max window)

Revision ID: 012
Revises: 011
Create Date: 2026-03-28
"""

from alembic import op
import sqlalchemy as sa

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_configs",
        sa.Column(
            "context_window",
            sa.Integer,
            nullable=True,
            comment="Model context window size in tokens. Used for usage ratio diagnostics.",
        ),
    )


def downgrade() -> None:
    op.drop_column("model_configs", "context_window")
