"""projects table

Revision ID: 0002_projects
Revises: 0001_baseline
Create Date: 2026-05-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_projects"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "ix_projects_created_at",
        "projects",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_projects_created_at", table_name="projects")
    op.drop_table("projects")
