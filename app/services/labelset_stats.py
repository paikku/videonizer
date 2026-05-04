"""LabelSet aggregate stats (real annotation-aware version since PR #6)."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ..storage.repo.annotations import AnnotationRepo, data_load


@dataclass
class LabelSetStats:
    image_count: int
    annotation_count: int
    labeled_image_count: int
    excluded_image_count: int
    class_stats: list[dict]


async def compute_stats(
    session: AsyncSession,
    *,
    image_ids: list[str],
    excluded_image_ids: list[str],
    classes: list[dict],  # noqa: ARG001 — currently unused; kept for API stability
    labelset_id: str,
) -> LabelSetStats:
    repo = AnnotationRepo(session)
    annotation_count = await repo.count_for_labelset(labelset_id)
    labeled_ids = await repo.distinct_labeled_image_ids(labelset_id)
    # labeled_image_count is the intersection with the labelset's member
    # images — annotations on excluded/non-member images shouldn't inflate
    # the "labeled fraction" reported in the UI.
    member_set = set(image_ids)
    labeled_in_members = labeled_ids & member_set if member_set else labeled_ids
    rows = await repo.class_stats(labelset_id)
    class_stats = [
        {
            "classId": cid,
            "imageCount": ic,
            "annotationCount": ac,
        }
        for cid, ic, ac in rows
    ]
    return LabelSetStats(
        image_count=len(image_ids),
        annotation_count=annotation_count,
        labeled_image_count=len(labeled_in_members),
        excluded_image_count=len(excluded_image_ids),
        class_stats=class_stats,
    )


async def compute_image_labels_and_shapes(
    session: AsyncSession,
    *,
    labelset_id: str,
) -> tuple[dict[str, list[str]], dict[str, list[dict]]]:
    """imageLabels: imageId → distinct classIds present on it.
    imageShapes: imageId → list of `{kind, ...geometry}` dicts.
    """
    repo = AnnotationRepo(session)
    rows = await repo.list_for_labelset(labelset_id)

    labels: dict[str, set[str]] = {}
    shapes: dict[str, list[dict]] = {}
    for r in rows:
        labels.setdefault(r.image_id, set()).add(r.class_id)
        d = data_load(r.data_json)
        entry = {"id": r.id, "classId": r.class_id, "kind": r.kind}
        if r.kind == "rect":
            entry["rect"] = {
                "x": d.get("x", 0.0),
                "y": d.get("y", 0.0),
                "w": d.get("w", 0.0),
                "h": d.get("h", 0.0),
            }
        elif r.kind == "polygon":
            entry["polygon"] = d.get("polygon") or []
        shapes.setdefault(r.image_id, []).append(entry)

    return (
        {iid: sorted(cs) for iid, cs in labels.items()},
        shapes,
    )
