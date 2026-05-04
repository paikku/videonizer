"""resources table

Revision ID: 0003_resources
Revises: 0002_projects
Create Date: 2026-05-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_resources"
down_revision = "0002_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resources",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("tags_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration", sa.Float(), nullable=True),
        sa.Column("ingest_via", sa.String(length=16), nullable=True),
        sa.Column("source_blob_key", sa.String(length=512), nullable=True),
        sa.Column("source_size", sa.BigInteger(), nullable=True),
        sa.Column("source_content_type", sa.String(length=120), nullable=True),
        sa.Column(
            "preview_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "type IN ('video', 'image_batch')", name="ck_resources_type"
        ),
    )
    op.create_index(
        "ix_resources_project_created",
        "resources",
        ["project_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_resources_project_created", table_name="resources")
    op.drop_table("resources")
