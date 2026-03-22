"""Add package metadata fields to skills table (Phase F1).

New columns: version, license, compatibility, metadata (JSONB),
source_type, source_path.
Replace unique(name) with unique(name, version).

Revision ID: 006
Revises: 005
Create Date: 2026-03-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── New columns ──
    op.add_column(
        "skills",
        sa.Column("version", sa.String(32), nullable=False, server_default="0.1.0"),
    )
    op.add_column(
        "skills",
        sa.Column("license", sa.String(128), nullable=False, server_default=""),
    )
    op.add_column(
        "skills",
        sa.Column("compatibility", sa.String(128), nullable=False, server_default=""),
    )
    op.add_column(
        "skills",
        sa.Column("metadata_extra", JSONB, nullable=False, server_default="{}"),
    )
    op.add_column(
        "skills",
        sa.Column(
            "source_type",
            sa.String(32),
            nullable=False,
            server_default="manual",
        ),
    )
    op.add_column(
        "skills",
        sa.Column("source_path", sa.Text, nullable=True),
    )

    # ── Replace unique(name) with unique(name, version) ──
    op.drop_constraint("skills_name_key", "skills", type_="unique")
    op.create_unique_constraint(
        "uq_skills_name_version", "skills", ["name", "version"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_skills_name_version", "skills", type_="unique")
    op.create_unique_constraint("skills_name_key", "skills", ["name"])

    op.drop_column("skills", "source_path")
    op.drop_column("skills", "source_type")
    op.drop_column("skills", "metadata_extra")
    op.drop_column("skills", "compatibility")
    op.drop_column("skills", "license")
    op.drop_column("skills", "version")
