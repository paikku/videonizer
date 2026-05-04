"""classId → dense index mapping used by every YOLO/CSV writer."""
from __future__ import annotations

from typing import Any


def build_class_mapping(
    classes: list[dict[str, Any]],
    annotations: list[dict[str, Any]],
    remap: bool,
) -> dict[str, Any]:
    """Returns ``{"classes": [...], "indexById": {classId: int}}``.

    * ``remap=False`` — every declared class survives in declaration order.
      Useful when the LabelSet definition itself is the authority.
    * ``remap=True``  — only classes referenced by at least one annotation
      survive, indexed densely 0..N-1 in original declaration order.
    """
    if not remap:
        return {
            "classes": list(classes),
            "indexById": {c["id"]: i for i, c in enumerate(classes)},
        }
    used = {a["classId"] for a in annotations}
    kept = [c for c in classes if c["id"] in used]
    return {
        "classes": kept,
        "indexById": {c["id"]: i for i, c in enumerate(kept)},
    }
