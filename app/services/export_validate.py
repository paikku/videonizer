"""Export validate (dry-run preflight)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ..domain.annotations import (
    ExportPreviewItem,
    SplitConfig,
    ValidationReport,
)
from ..storage.repo.annotations import AnnotationRepo
from ..storage.repo.images import Image, ImageRepo, _tags_load
from ..storage.repo.labelsets import LabelSet, _list_load


@dataclass
class ValidateResult:
    report: ValidationReport
    items: list[ExportPreviewItem]


def _assign_split(image_id: str, split: SplitConfig) -> str:
    """Deterministic per-image assignment. Hash → bucket so the same image
    ends up in the same split across export reruns.
    """
    h = int(hashlib.sha1(image_id.encode("utf-8")).hexdigest(), 16) / (1 << 160)
    if h < split.train:
        return "train"
    if h < split.train + split.val:
        return "val"
    return "test"


async def validate(
    session: AsyncSession,
    *,
    labelset: LabelSet,
    split: SplitConfig | None,
) -> ValidateResult:
    image_ids = [str(x) for x in _list_load(labelset.image_ids_json)]
    excluded = set(str(x) for x in _list_load(labelset.excluded_image_ids_json))

    img_repo = ImageRepo(session)
    images: dict[str, Image] = {}
    for iid in image_ids:
        row = await img_repo.get_by_id(iid)
        if row is not None:
            images[iid] = row

    labeled_ids = await AnnotationRepo(session).distinct_labeled_image_ids(
        labelset.id
    )

    effective_split = split or SplitConfig()
    items: list[ExportPreviewItem] = []
    split_counts: dict[str, int] = {"train": 0, "val": 0, "test": 0}
    labeled_count = 0
    for iid in image_ids:
        img = images.get(iid)
        if img is None:
            # Image may have been deleted; surface as a warning row.
            items.append(
                ExportPreviewItem(
                    image_id=iid,
                    file_name="",
                    split=None,
                    excluded=True,
                    labeled=False,
                    tags=[],
                )
            )
            continue
        is_excluded = iid in excluded
        is_labeled = iid in labeled_ids
        if is_labeled:
            labeled_count += 1
        split_name: str | None = None
        if not is_excluded:
            split_name = _assign_split(iid, effective_split)
            split_counts[split_name] += 1
        items.append(
            ExportPreviewItem(
                image_id=iid,
                file_name=img.file_name,
                split=split_name,  # type: ignore[arg-type]
                excluded=is_excluded,
                labeled=is_labeled,
                tags=_tags_load(img.tags_json),
            )
        )

    warnings: list[str] = []
    missing = sum(1 for it in items if it.file_name == "")
    if missing:
        warnings.append(f"{missing} member image(s) no longer exist")
    unlabeled_in_train = sum(
        1 for it in items if it.split == "train" and not it.labeled
    )
    if unlabeled_in_train:
        warnings.append(
            f"{unlabeled_in_train} unlabeled image(s) will land in the train split"
        )

    report = ValidationReport(
        total_images=len(image_ids),
        labeled_images=labeled_count,
        unlabeled_images=len(image_ids) - labeled_count,
        split_counts=split_counts,
        warnings=warnings,
    )
    return ValidateResult(report=report, items=items)
