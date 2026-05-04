"""labelsets table

Revision ID: 0005_labelsets
Revises: 0004_images
Create Date: 2026-05-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_labelsets"
down_revision = "0004_images"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "labelsets",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "classes_json", sa.Text(), nullable=False, server_default="[]"
        ),
        sa.Column(
            "image_ids_json", sa.Text(), nullable=False, server_default="[]"
        ),
        sa.Column(
            "excluded_image_ids_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "type IN ('polygon', 'bbox', 'classify')", name="ck_labelsets_type"
        ),
    )
    op.create_index(
        "ix_labelsets_project_created",
        "labelsets",
        ["project_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_labelsets_project_created", table_name="labelsets")
    op.drop_table("labelsets")
