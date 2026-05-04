"""LabelSet + annotation CRUD.

``mutate_labelset`` / ``mutate_labelset_annotations`` are locked
read-modify-write helpers used by both update endpoints and cascade
deletes (see ``images.delete_image``). The mutator may mutate the value
in place; a return of exactly ``False`` skips the write so a no-op pass
over all labelsets doesn't churn timestamps.
"""
from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from .io import ensure_dir, read_json, with_file_lock, write_json
from .ids import gen_id
from .paths import labelset_dir, labelsets_index


async def _read_index_list(path: Path) -> list[str]:
    return await read_json(path, [])


async def _append_index_id(path: Path, item_id: str) -> None:
    async def fn() -> None:
        ids = await _read_index_list(path)
        if item_id not in ids:
            ids.append(item_id)
        await write_json(path, ids)

    await with_file_lock(path, fn)


async def _remove_index_id(path: Path, item_id: str) -> None:
    async def fn() -> None:
        ids = await _read_index_list(path)
        await write_json(path, [x for x in ids if x != item_id])

    await with_file_lock(path, fn)


async def list_labelsets(project_id: str) -> list[dict[str, Any]]:
    """Return ``LabelSetSummary`` objects with aggregate counts computed on
    the fly from each labelset's annotations file.
    """
    ids = await _read_index_list(labelsets_index(project_id))
    out: list[dict[str, Any]] = []
    for lsid in ids:
        ls = await get_labelset(project_id, lsid)
        if ls is None:
            continue
        ann = await get_labelset_annotations(project_id, lsid)
        member = set(ls.get("imageIds", []))
        excluded = set(ls.get("excludedImageIds") or [])
        labeled: set[str] = set()
        per_class: dict[str, set[str]] = {}
        per_image: dict[str, set[str]] = {}
        image_shapes: dict[str, list[dict[str, Any]]] = {}
        for a in ann["annotations"]:
            iid = a["imageId"]
            if iid not in member:
                continue
            labeled.add(iid)
            per_class.setdefault(a["classId"], set()).add(iid)
            per_image.setdefault(iid, set()).add(a["classId"])
            if a["kind"] in ("rect", "polygon"):
                image_shapes.setdefault(iid, []).append(
                    {"classId": a["classId"], "shape": a["shape"]}
                )
        class_stats = [
            {
                "classId": c["id"],
                "imageCount": len(per_class.get(c["id"], set())),
            }
            for c in ls["classes"]
        ]
        image_labels = {iid: list(cs) for iid, cs in per_image.items()}
        out.append(
            {
                "id": ls["id"],
                "name": ls["name"],
                "type": ls["type"],
                "description": ls.get("description"),
                "classes": ls["classes"],
                "imageIds": ls.get("imageIds", []),
                "excludedImageIds": ls.get("excludedImageIds") or [],
                "imageCount": len(ls.get("imageIds", [])),
                "annotationCount": len(ann["annotations"]),
                "labeledImageCount": len(labeled),
                "excludedImageCount": len(excluded),
                "classStats": class_stats,
                "imageLabels": image_labels,
                "imageShapes": image_shapes,
                "createdAt": ls["createdAt"],
            }
        )
    out.sort(key=lambda s: s["createdAt"])
    return out


async def get_labelset(project_id: str, labelset_id: str) -> dict[str, Any] | None:
    return await read_json(labelset_dir(project_id, labelset_id) / "meta.json", None)


async def create_labelset(
    project_id: str,
    *,
    name: str,
    type: str,
    description: str | None = None,
    classes: list[dict[str, Any]] | None = None,
    image_ids: list[str] | None = None,
) -> dict[str, Any]:
    if type not in ("polygon", "bbox", "classify"):
        raise ValueError(f"invalid labelset type: {type!r}")
    lsid = gen_id()
    desc = (description or "").strip() or None
    labelset: dict[str, Any] = {
        "id": lsid,
        "name": (name or "").strip() or "Untitled",
        "type": type,
        "description": desc,
        "classes": list(classes or []),
        "imageIds": list(image_ids or []),
        "excludedImageIds": [],
        "createdAt": int(time.time() * 1000),
    }
    d = labelset_dir(project_id, lsid)
    await ensure_dir(d)
    await write_json(d / "meta.json", labelset)
    await write_json(d / "annotations.json", {"annotations": []})
    await _append_index_id(labelsets_index(project_id), lsid)
    return labelset


async def mutate_labelset(
    project_id: str,
    labelset_id: str,
    mutator: Callable[[dict[str, Any]], Any],
) -> dict[str, Any] | None:
    """Locked read-modify-write on the labelset meta. The mutator receives
    the current labelset dict and may mutate it in place. A return of
    exactly ``False`` skips the write.
    """
    file = labelset_dir(project_id, labelset_id) / "meta.json"

    async def fn() -> dict[str, Any] | None:
        current = await get_labelset(project_id, labelset_id)
        if current is None:
            return None
        result = mutator(current)
        if asyncio.iscoroutine(result):
            result = await result
        if result is False:
            return current
        await write_json(file, current)
        return current

    return await with_file_lock(file, fn)


async def update_labelset(
    project_id: str,
    labelset_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    classes: list[dict[str, Any]] | None = None,
    image_ids: list[str] | None = None,
    excluded_image_ids: list[str] | None = None,
) -> dict[str, Any] | None:
    def m(ls: dict[str, Any]) -> None:
        if name is not None:
            stripped = name.strip()
            if stripped:
                ls["name"] = stripped
        if description is not None:
            stripped = description.strip()
            ls["description"] = stripped or None
        if classes is not None:
            ls["classes"] = list(classes)
        if image_ids is not None:
            ls["imageIds"] = list(image_ids)
        if excluded_image_ids is not None:
            ls["excludedImageIds"] = list(excluded_image_ids)

    return await mutate_labelset(project_id, labelset_id, m)


async def delete_labelset(project_id: str, labelset_id: str) -> None:
    await _remove_index_id(labelsets_index(project_id), labelset_id)
    await asyncio.to_thread(
        shutil.rmtree, labelset_dir(project_id, labelset_id), ignore_errors=True
    )


def _annotations_path(project_id: str, labelset_id: str) -> Path:
    return labelset_dir(project_id, labelset_id) / "annotations.json"


async def get_labelset_annotations(
    project_id: str, labelset_id: str
) -> dict[str, Any]:
    return await read_json(
        _annotations_path(project_id, labelset_id), {"annotations": []}
    )


async def save_labelset_annotations(
    project_id: str, labelset_id: str, data: dict[str, Any]
) -> None:
    await write_json(_annotations_path(project_id, labelset_id), data)


async def mutate_labelset_annotations(
    project_id: str,
    labelset_id: str,
    mutator: Callable[[dict[str, Any]], Any],
) -> Any:
    """Locked RMW on annotations.json. The mutator may modify ``data`` in
    place; a return of exactly ``False`` skips the write.
    """
    file = _annotations_path(project_id, labelset_id)

    async def fn() -> Any:
        data = await get_labelset_annotations(project_id, labelset_id)
        result = mutator(data)
        if asyncio.iscoroutine(result):
            result = await result
        if result is not False:
            await write_json(file, data)
        return result

    return await with_file_lock(file, fn)
