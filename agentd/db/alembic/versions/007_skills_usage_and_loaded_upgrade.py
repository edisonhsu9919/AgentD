"""Add usage_count/last_used_at to skills; migrate sessions.loaded_skills
from string array to object array [{name, version}] (Phase F2).

Revision ID: 007
Revises: 006
Create Date: 2026-03-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Skills: usage statistics ──
    op.add_column(
        "skills",
        sa.Column("usage_count", sa.BigInteger, nullable=False, server_default="0"),
    )
    op.add_column(
        "skills",
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── Sessions: migrate loaded_skills from ["name"] to [{"name":"..","version":".."}] ──
    # The column is already JSONB. We convert existing string arrays in-place.
    op.execute("""
        UPDATE sessions
        SET loaded_skills = (
            SELECT COALESCE(
                jsonb_agg(
                    jsonb_build_object('name', elem::text, 'version', '0.1.0')
                ),
                '[]'::jsonb
            )
            FROM jsonb_array_elements_text(loaded_skills) AS elem
        )
        WHERE jsonb_typeof(loaded_skills) = 'array'
          AND jsonb_array_length(loaded_skills) > 0
          AND jsonb_typeof(loaded_skills->0) = 'string'
    """)


def downgrade() -> None:
    # ── Sessions: revert loaded_skills from objects back to strings ──
    op.execute("""
        UPDATE sessions
        SET loaded_skills = (
            SELECT COALESCE(
                jsonb_agg(elem->>'name'),
                '[]'::jsonb
            )
            FROM jsonb_array_elements(loaded_skills) AS elem
        )
        WHERE jsonb_typeof(loaded_skills) = 'array'
          AND jsonb_array_length(loaded_skills) > 0
          AND jsonb_typeof(loaded_skills->0) = 'object'
    """)

    op.drop_column("skills", "last_used_at")
    op.drop_column("skills", "usage_count")
