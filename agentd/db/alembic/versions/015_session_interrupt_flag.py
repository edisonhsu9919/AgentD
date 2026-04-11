"""015: Add interrupt_requested_at to sessions table (Phase 7A).

Supports session-level abort signalling for the concurrent worker model.
When non-null, indicates an abort has been requested. Running workers
check this flag at each tool boundary instead of relying on a queued
abort run (which may be claimed by a different worker).
"""

import sqlalchemy as sa
from alembic import op


revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "interrupt_requested_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("sessions", "interrupt_requested_at")
