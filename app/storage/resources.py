"""Resource CRUD plus video-resource-specific source/preview IO.

A Resource is the upload unit; Images derived from it carry the actual
labeling work. Deleting a Resource cascade-deletes its derived Images
(which in turn strip their annotations and labelset memberships).
"""
from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Any, Sequence

from .io import (
    ensure_dir,
    read_bytes,
    read_json,
    unlink_quiet,
    with_file_lock,
    write_bytes_atomic,
    write_json,
)
from .ids import gen_id
from .paths import preview_path, resource_dir, resources_index


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


async def list_resources(project_id: str) -> list[dict[str, Any]]:
    """All resources in a project + their derived image counts. Sorted by createdAt."""
    from .images import list_images  # local import to break the import cycle

    ids = await _read_index_list(resources_index(project_id))
    all_images = await list_images(project_id)
    counts: dict[str, int] = {}
    for img in all_images:
        counts[img["resourceId"]] = counts.get(img["resourceId"], 0) + 1
    out: list[dict[str, Any]] = []
    for rid in ids:
        r = await get_resource(project_id, rid)
        if r is not None:
            out.append({**r, "imageCount": counts.get(rid, 0)})
    out.sort(key=lambda r: r["createdAt"])
    return out


async def get_resource(project_id: str, resource_id: str) -> dict[str, Any] | None:
    return await read_json(resource_dir(project_id, resource_id) / "meta.json", None)


async def create_resource(
    project_id: str,
    *,
    type: str,
    name: str,
    tags: list[str] | None = None,
    source_ext: str | None = None,
    source_buffer: bytes | None = None,
    width: int | None = None,
    height: int | None = None,
    duration: float | None = None,
    ingest_via: str | None = None,
) -> dict[str, Any]:
    rid = gen_id()
    base: dict[str, Any] = {
        "id": rid,
        "type": type,
        "name": (name or "").strip() or "Untitled",
        "tags": list(tags or []),
        "createdAt": int(time.time() * 1000),
    }
    if type == "video":
        if (
            source_ext is None
            or source_buffer is None
            or width is None
            or height is None
        ):
            raise ValueError(
                "video resource requires source_ext, source_buffer, width, height"
            )
        resource: dict[str, Any] = {
            **base,
            "sourceExt": source_ext,
            "width": width,
            "height": height,
            "duration": duration,
            "ingestVia": ingest_via,
            "previewCount": 0,
        }
    elif type == "image_batch":
        resource = base
    else:
        raise ValueError(f"unknown resource type: {type!r}")

    rdir = resource_dir(project_id, rid)
    await ensure_dir(rdir)
    if type == "video":
        await write_bytes_atomic(rdir / f"source.{source_ext}", source_buffer)
    await write_json(rdir / "meta.json", resource)
    await _append_index_id(resources_index(project_id), rid)
    return resource


async def update_resource(
    project_id: str,
    resource_id: str,
    *,
    name: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any] | None:
    file = resource_dir(project_id, resource_id) / "meta.json"

    async def fn() -> dict[str, Any] | None:
        current = await get_resource(project_id, resource_id)
        if current is None:
            return None
        next_ = dict(current)
        if name is not None:
            stripped = name.strip()
            if stripped:
                next_["name"] = stripped
        if tags is not None:
            next_["tags"] = list(tags)
        await write_json(file, next_)
        return next_

    return await with_file_lock(file, fn)


async def delete_resource(project_id: str, resource_id: str) -> None:
    """Cascade: drops every Image whose ``resourceId`` matches, which in turn
    strips that image from labelset memberships and any annotations against
    it. Then removes the resource directory and index entry.
    """
    from .images import delete_image, list_images

    images = await list_images(project_id, resource_id=resource_id)
    for img in images:
        await delete_image(project_id, img["id"])
    await _remove_index_id(resources_index(project_id), resource_id)
    await asyncio.to_thread(
        shutil.rmtree, resource_dir(project_id, resource_id), ignore_errors=True
    )


async def read_resource_source(
    project_id: str, resource_id: str
) -> tuple[bytes, str] | None:
    r = await get_resource(project_id, resource_id)
    if r is None or r.get("type") != "video" or not r.get("sourceExt"):
        return None
    p = resource_dir(project_id, resource_id) / f"source.{r['sourceExt']}"
    data = await read_bytes(p)
    if data is None:
        return None
    return data, r["sourceExt"]


async def stat_resource_source(
    project_id: str, resource_id: str
) -> dict[str, Any] | None:
    """Return on-disk metadata for the source file. Lets the streaming Range
    handler open the file directly without materializing it in memory.
    """
    r = await get_resource(project_id, resource_id)
    if r is None or r.get("type") != "video" or not r.get("sourceExt"):
        return None
    p = resource_dir(project_id, resource_id) / f"source.{r['sourceExt']}"
    try:
        st = await asyncio.to_thread(p.stat)
    except FileNotFoundError:
        return None
    return {"path": p, "size": st.st_size, "ext": r["sourceExt"]}


async def write_previews(
    project_id: str, resource_id: str, buffers: Sequence[bytes]
) -> int:
    """Replace the preview reel atomically. Stale frames beyond the new set
    are removed so the directory matches the new ``previewCount`` exactly.
    """
    meta_file = resource_dir(project_id, resource_id) / "meta.json"

    async def fn() -> int:
        r = await get_resource(project_id, resource_id)
        if r is None:
            raise FileNotFoundError(f"resource not found: {resource_id}")
        if r.get("type") != "video":
            raise ValueError("previews only valid on video resources")
        await ensure_dir(preview_path(project_id, resource_id, 0).parent)
        old_count = int(r.get("previewCount") or 0)
        for i in range(max(old_count, len(buffers))):
            await unlink_quiet(preview_path(project_id, resource_id, i))
        for i, buf in enumerate(buffers):
            await write_bytes_atomic(
                preview_path(project_id, resource_id, i), buf
            )
        next_ = {**r, "previewCount": len(buffers)}
        await write_json(meta_file, next_)
        return len(buffers)

    return await with_file_lock(meta_file, fn)


async def read_preview(
    project_id: str, resource_id: str, idx: int
) -> bytes | None:
    if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0:
        return None
    return await read_bytes(preview_path(project_id, resource_id, idx))
