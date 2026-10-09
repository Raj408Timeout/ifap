"""Create the questionnaires table.

Revision ID: 0001
Revises:
Create Date: 2026-10-08
"""

# pylint: disable=invalid-name  # Alembic requires numbered file names and lowercase revision ids

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "questionnaires",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("survey_type", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("survey_type", "status", "updated_at"):
        op.create_index(f"ix_questionnaires_{column}", "questionnaires", [column])


def downgrade() -> None:
    op.drop_table("questionnaires")
