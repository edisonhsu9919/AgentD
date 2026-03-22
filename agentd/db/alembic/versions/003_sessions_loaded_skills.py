"""Add loaded_skills JSONB column to sessions table.

Revision ID: 003
Revises: 002
Create Date: 2026-03-14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("loaded_skills", JSONB, nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("sessions", "loaded_skills")
