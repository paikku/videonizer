"""baseline (empty)

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-04

PR #1 ships no tables. This baseline only establishes the alembic version
table on a fresh database so subsequent PRs can `revises="0001_baseline"`.
"""
from __future__ import annotations

from alembic import op  # noqa: F401  (unused; intentional)
import sqlalchemy as sa  # noqa: F401


revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Intentionally empty — no schema in PR #1.
    pass


def downgrade() -> None:
    pass
