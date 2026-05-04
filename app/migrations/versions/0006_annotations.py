"""annotations table

Revision ID: 0006_annotations
Revises: 0005_labelsets
Create Date: 2026-05-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_annotations"
down_revision = "0005_labelsets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "annotations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "labelset_id",
            sa.String(length=36),
            sa.ForeignKey("labelsets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "image_id",
            sa.String(length=36),
            sa.ForeignKey("images.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("class_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("data_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('rect', 'polygon', 'classify')", name="ck_annotations_kind"
        ),
    )
    op.create_index(
        "ix_annotations_labelset_image",
        "annotations",
        ["labelset_id", "image_id"],
    )
    op.create_index(
        "ix_annotations_labelset_class",
        "annotations",
        ["labelset_id", "class_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_annotations_labelset_class", table_name="annotations"
    )
    op.drop_index(
        "ix_annotations_labelset_image", table_name="annotations"
    )
    op.drop_table("annotations")
