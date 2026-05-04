"""images table

Revision ID: 0004_images
Revises: 0003_resources
Create Date: 2026-05-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_images"
down_revision = "0003_resources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "images",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "resource_id",
            sa.String(length=36),
            sa.ForeignKey("resources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.Float(), nullable=True),
        sa.Column("frame_index", sa.Integer(), nullable=True),
        sa.Column("tags_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("bytes_blob_key", sa.String(length=512), nullable=False),
        sa.Column(
            "bytes_size", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("bytes_content_type", sa.String(length=120), nullable=False),
        sa.Column("thumb_blob_key", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "source IN ('uploaded', 'video_frame')", name="ck_images_source"
        ),
    )
    op.create_index(
        "ix_images_project_resource",
        "images",
        ["project_id", "resource_id"],
    )
    op.create_index(
        "ix_images_project_created",
        "images",
        ["project_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_images_project_created", table_name="images")
    op.drop_index("ix_images_project_resource", table_name="images")
    op.drop_table("images")
