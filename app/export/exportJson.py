"""LabelSet → single JSON document.

Image bytes are not inlined; consumers fetch them via
``/v1/projects/{id}/images/{iid}/bytes`` if they need pixels.
"""
from __future__ import annotations

from typing import Any


def build_labelset_export(
    *,
    labelset: dict[str, Any],
    images: list[dict[str, Any]],
    resources: list[dict[str, Any]],
    annotations: list[dict[str, Any]],
) -> dict[str, Any]:
    resource_by_id = {r["id"]: r for r in resources}
    # Preserve the LabelSet's declared image order.
    order = {iid: i for i, iid in enumerate(labelset.get("imageIds", []))}
    sorted_images = sorted(images, key=lambda img: order.get(img["id"], 0))

    out_images: list[dict[str, Any]] = []
    for img in sorted_images:
        r = resource_by_id.get(img.get("resourceId"))
        entry: dict[str, Any] = {
            "id": img["id"],
            "fileName": img["fileName"],
            "width": img["width"],
            "height": img["height"],
            "source": img["source"],
            "resource": (
                {"id": r["id"], "name": r["name"], "type": r["type"]}
                if r is not None
                else None
            ),
            "tags": img.get("tags", []),
        }
        if img.get("videoFrameMeta") is not None:
            entry["videoFrameMeta"] = img["videoFrameMeta"]
        out_images.append(entry)

    return {
        "version": 2,
        "labelSet": {
            "id": labelset["id"],
            "name": labelset["name"],
            "type": labelset["type"],
            "classes": labelset.get("classes", []),
            "createdAt": labelset["createdAt"],
        },
        "images": out_images,
        "annotations": annotations,
    }
