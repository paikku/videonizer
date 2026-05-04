"""LabelSet aggregate stats.

PR #5: annotations table doesn't exist yet, so all annotation-derived
stats return zero/empty. PR #6 swaps in real GROUP BY queries.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class LabelSetStats:
    image_count: int
    annotation_count: int
    labeled_image_count: int
    excluded_image_count: int
    class_stats: list[dict]


async def compute_stats(
    session: AsyncSession,  # noqa: ARG001 — used in PR #6
    *,
    image_ids: list[str],
    excluded_image_ids: list[str],
    classes: list[dict],  # noqa: ARG001 — used in PR #6
    labelset_id: str,  # noqa: ARG001 — used in PR #6
) -> LabelSetStats:
    return LabelSetStats(
        image_count=len(image_ids),
        annotation_count=0,  # PR #6
        labeled_image_count=0,  # PR #6
        excluded_image_count=len(excluded_image_ids),
        class_stats=[],  # PR #6
    )


async def compute_image_labels_and_shapes(
    session: AsyncSession,  # noqa: ARG001
    *,
    labelset_id: str,  # noqa: ARG001
) -> tuple[dict[str, list[str]], dict[str, list[dict]]]:
    """Heavy fields shipped only via /summary. Empty until PR #6."""
    return {}, {}
