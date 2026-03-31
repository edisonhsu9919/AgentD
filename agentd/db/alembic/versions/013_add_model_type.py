"""Add model_type column to model_configs (Phase O3-1).

Distinguishes LLM from VLM configurations in the same table.
Existing rows default to 'llm'. is_default becomes type-scoped:
one default LLM + one default VLM can coexist.

Revision ID: 013
Revises: 012
Create Date: 2026-03-31
"""

from alembic import op
import sqlalchemy as sa

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_configs",
        sa.Column(
            "model_type",
            sa.String(16),
            nullable=False,
            server_default="llm",
            comment="Model type: 'llm' for text models, 'vlm' for vision-language models.",
        ),
    )


def downgrade() -> None:
    op.drop_column("model_configs", "model_type")
