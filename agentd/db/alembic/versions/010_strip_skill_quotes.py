"""Strip extra YAML quotes from skills.name/description and user_skills.skill_name (Phase K).

Historical import-local records may have retained surrounding quotes from
frontmatter values (e.g. '"doc"' instead of 'doc').  The parser is fixed;
this migration cleans up existing data.

Revision ID: 010
Revises: 009
Create Date: 2026-03-21
"""

from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Strip double quotes from skills.name
    conn.execute(
        sa_text(
            "UPDATE skills SET name = TRIM(BOTH '\"' FROM name) "
            "WHERE name LIKE '\"%' AND name LIKE '%\"'"
        )
    )
    # Strip single quotes from skills.name
    conn.execute(
        sa_text(
            "UPDATE skills SET name = TRIM(BOTH '''' FROM name) "
            "WHERE name LIKE '''%' AND name LIKE '%'''"
        )
    )

    # Strip double quotes from skills.description
    conn.execute(
        sa_text(
            "UPDATE skills SET description = TRIM(BOTH '\"' FROM description) "
            "WHERE description LIKE '\"%' AND description LIKE '%\"'"
        )
    )
    # Strip single quotes from skills.description
    conn.execute(
        sa_text(
            "UPDATE skills SET description = TRIM(BOTH '''' FROM description) "
            "WHERE description LIKE '''%' AND description LIKE '%'''"
        )
    )

    # Strip double quotes from user_skills.skill_name (references skills.name)
    conn.execute(
        sa_text(
            "UPDATE user_skills SET skill_name = TRIM(BOTH '\"' FROM skill_name) "
            "WHERE skill_name LIKE '\"%' AND skill_name LIKE '%\"'"
        )
    )
    # Strip single quotes from user_skills.skill_name
    conn.execute(
        sa_text(
            "UPDATE user_skills SET skill_name = TRIM(BOTH '''' FROM skill_name) "
            "WHERE skill_name LIKE '''%' AND skill_name LIKE '%'''"
        )
    )


def downgrade() -> None:
    # Data-only migration — no structural rollback needed
    pass


# Import here to avoid top-level dependency issues in some alembic envs
from sqlalchemy import text as sa_text
