"""016: Add internal session visibility flag.

Internal task sessions stay queryable by feature-specific APIs, but are hidden
from the ordinary user session list.
"""

import sqlalchemy as sa
from alembic import op


revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "is_internal",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("sessions", "is_internal")
