"""Dry-run validation: counts, warnings, format pick.

Inputs are already filtered to "what would be exported" — the bundle
builder uses the same filter and reuses ``validate_export`` so the
preview never disagrees with the actual ZIP that ships.
"""
from __future__ import annotations

from typing import Any


# Format chosen by LabelSet type. Frozen — see API_CONTRACT §6.
def format_for_labelset_type(t: str) -> str:
    if t == "bbox":
        return "yolo-detection"
    if t == "polygon":
        return "yolo-segmentation"
    return "classify-csv"


def validate_export(
    *,
    labelset: dict[str, Any],
    images: list[dict[str, Any]],
    annotations: list[dict[str, Any]],
    splits: dict[str, str | None],
) -> dict[str, Any]:
    fmt = format_for_labelset_type(labelset["type"])

    excluded = set(labelset.get("excludedImageIds") or [])
    labeled_image_ids = {a["imageId"] for a in annotations}

    def is_usable(iid: str) -> bool:
        return iid in labeled_image_ids or iid in excluded

    split_counts: dict[str, int] = {"train": 0, "val": 0, "test": 0, "unassigned": 0}
    usable = 0
    unusable = 0
    for img in images:
        if not is_usable(img["id"]):
            unusable += 1
            continue
        usable += 1
        s = splits.get(img["id"])
        if s is None:
            split_counts["unassigned"] += 1
        else:
            split_counts[s] = split_counts.get(s, 0) + 1

    warnings: list[dict[str, Any]] = []

    if not labelset.get("classes"):
        warnings.append(
            {"code": "no-classes", "message": "LabelSet 에 클래스가 정의되지 않았습니다."}
        )

    if not annotations:
        warnings.append(
            {"code": "no-annotations", "message": "어노테이션이 하나도 없습니다."}
        )

    if unusable > 0:
        warnings.append(
            {
                "code": "unusable-images",
                "message": (
                    f"{unusable}장이 미라벨이라 export 에서 제외됩니다. "
                    "라벨링을 끝내거나 LabelSet 에서 제외 처리하세요."
                ),
            }
        )

    if split_counts["unassigned"] > 0:
        warnings.append(
            {
                "code": "unassigned-split",
                "message": (
                    f"{split_counts['unassigned']}장의 이미지가 어느 split 에도 "
                    "속하지 않아 export 에서 제외됩니다."
                ),
            }
        )

    if fmt == "classify-csv":
        labels_by_image: dict[str, set[str]] = {}
        for a in annotations:
            if a.get("kind") != "classify":
                continue
            labels_by_image.setdefault(a["imageId"], set()).add(a["classId"])
        multi = sum(1 for s in labels_by_image.values() if len(s) > 1)
        if multi > 0:
            warnings.append(
                {
                    "code": "multi-class-classify",
                    "message": (
                        f"{multi}장의 이미지에 여러 클래스가 지정되어 있습니다. "
                        "CSV 에서는 각각 별도 행으로 출력됩니다."
                    ),
                }
            )
    else:
        out_of_bounds = 0
        for a in annotations:
            kind = a.get("kind")
            if kind == "rect":
                shape = a.get("shape", {})
                x, y, w, h = (
                    shape.get("x", 0),
                    shape.get("y", 0),
                    shape.get("w", 0),
                    shape.get("h", 0),
                )
                if (
                    x < 0
                    or y < 0
                    or x + w > 1 + 1e-6
                    or y + h > 1 + 1e-6
                    or w <= 0
                    or h <= 0
                ):
                    out_of_bounds += 1
            elif kind == "polygon":
                shape = a.get("shape", {})
                for ring in shape.get("rings", []):
                    for p in ring:
                        if (
                            p.get("x", 0) < -1e-6
                            or p.get("y", 0) < -1e-6
                            or p.get("x", 0) > 1 + 1e-6
                            or p.get("y", 0) > 1 + 1e-6
                        ):
                            out_of_bounds += 1
        if out_of_bounds > 0:
            warnings.append(
                {
                    "code": "out-of-bounds",
                    "message": (
                        f"{out_of_bounds}개의 좌표가 정규화 범위 [0, 1] 을 "
                        "벗어났습니다. export 시 클램프됩니다."
                    ),
                }
            )

    return {
        "format": fmt,
        "totalImages": len(images),
        "usableImages": usable,
        "unusableImages": unusable,
        "excludedImages": len(excluded),
        "annotationCount": len(annotations),
        "classCount": len(labelset.get("classes", [])),
        "splitCounts": split_counts,
        "warnings": warnings,
    }
