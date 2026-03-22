"""Drop tool_calls FK from permission_requests, change tool_call_id to nullable TEXT.

The original schema links permission_requests.tool_call_id → tool_calls.id,
but tool_calls requires message_id FK to messages — which are not persisted
until after the graph run completes. This makes it impossible to create
permission_request records during graph execution (interrupt).

Fix: store the LangChain tool_call_id as a plain TEXT column instead.

Revision ID: 002
Revises: 001
Create Date: 2026-03-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: str = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the FK constraint on tool_call_id
    op.drop_constraint(
        "permission_requests_tool_call_id_fkey",
        "permission_requests",
        type_="foreignkey",
    )
    # Change column type from UUID NOT NULL to TEXT nullable
    op.alter_column(
        "permission_requests",
        "tool_call_id",
        type_=sa.Text,
        nullable=True,
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "permission_requests",
        "tool_call_id",
        type_=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
        existing_type=sa.Text,
        existing_nullable=True,
    )
    op.create_foreign_key(
        "permission_requests_tool_call_id_fkey",
        "permission_requests",
        "tool_calls",
        ["tool_call_id"],
        ["id"],
    )
