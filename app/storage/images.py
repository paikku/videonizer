"""Image CRUD, bulk tag mutations, and lazy thumbnail generation.

Thumbnails are generated on first read and cached to disk; concurrent
first-access callers serialize on the per-thumb-path lock so we never
encode the same thumbnail twice.
"""
from __future__ import annotations

import asyncio
import io
import shutil
import time
from pathlib import Path
from typing import Any

from PIL import Image as PILImage, ImageOps

from .io import (
    ensure_dir,
    read_bytes,
    read_json,
    with_file_lock,
    write_bytes_atomic,
    write_json,
)
from .ids import gen_id
from .paths import (
    image_dir,
    images_index,
    labelsets_index,
    safe_id,
)

# Canonical thumbnail edge length. One size keeps storage simple and is
# large enough for 96px cards on HiDPI displays.
_THUMB_MAX = 384


def _thumb_path(project_id: str, image_id: str) -> Path:
    return image_dir(project_id, image_id) / f"thumb-{_THUMB_MAX}.jpg"


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


async def list_images(
    project_id: str,
    *,
    resource_id: str | None = None,
    source: str | None = None,
    tag: str | None = None,
) -> list[dict[str, Any]]:
    ids = await _read_index_list(images_index(project_id))
    out: list[dict[str, Any]] = []
    for iid in ids:
        img = await get_image(project_id, iid)
        if img is None:
            continue
        if resource_id is not None and img.get("resourceId") != resource_id:
            continue
        if source is not None and img.get("source") != source:
            continue
        if tag is not None and tag not in img.get("tags", []):
            continue
        out.append(img)
    return out


async def get_image(project_id: str, image_id: str) -> dict[str, Any] | None:
    return await read_json(image_dir(project_id, image_id) / "meta.json", None)


async def create_image(
    project_id: str,
    *,
    resource_id: str,
    source: str,
    file_name: str,
    ext: str,
    width: int,
    height: int,
    bytes_: bytes,
    tags: list[str] | None = None,
    video_frame_meta: dict[str, Any] | None = None,
    image_id: str | None = None,
) -> dict[str, Any]:
    iid = image_id or gen_id()
    safe_id(iid)
    image: dict[str, Any] = {
        "id": iid,
        "resourceId": resource_id,
        "source": source,
        "fileName": file_name,
        "ext": ext,
        "width": width,
        "height": height,
        "tags": list(tags or []),
        "createdAt": int(time.time() * 1000),
    }
    if video_frame_meta is not None:
        image["videoFrameMeta"] = video_frame_meta
    idir = image_dir(project_id, iid)
    await ensure_dir(idir)
    await write_bytes_atomic(idir / f"bytes.{ext}", bytes_)
    await write_json(idir / "meta.json", image)
    await _append_index_id(images_index(project_id), iid)
    return image


async def update_image(
    project_id: str,
    image_id: str,
    *,
    tags: list[str] | None = None,
) -> dict[str, Any] | None:
    file = image_dir(project_id, image_id) / "meta.json"

    async def fn() -> dict[str, Any] | None:
        current = await get_image(project_id, image_id)
        if current is None:
            return None
        next_ = dict(current)
        if tags is not None:
            next_["tags"] = list(tags)
        await write_json(file, next_)
        return next_

    return await with_file_lock(file, fn)


async def bulk_tag_images(
    project_id: str,
    image_ids: list[str],
    tags: list[str],
    mode: str,
) -> dict[str, int]:
    """Bulk tag mutation. ``mode``:

    * ``replace`` — overwrite tag list with ``tags``.
    * ``add``     — union of current tags and ``tags``.
    * ``remove``  — current tags minus ``tags``.

    Per-image meta is locked on the same lock as ``update_image`` so
    concurrent bulk + single edits cannot clobber each other.
    """
    if mode not in ("replace", "add", "remove"):
        raise ValueError(f"invalid mode: {mode!r}")
    updated = 0
    for iid in image_ids:
        file = image_dir(project_id, iid) / "meta.json"

        async def fn(iid: str = iid, file: Path = file) -> bool:
            current = await get_image(project_id, iid)
            if current is None:
                return False
            current_tags: list[str] = list(current.get("tags", []))
            if mode == "replace":
                next_tags = list(dict.fromkeys(tags))
            elif mode == "add":
                merged: dict[str, None] = {}
                for t in current_tags + list(tags):
                    merged.setdefault(t, None)
                next_tags = list(merged.keys())
            else:  # remove
                drop = set(tags)
                next_tags = [t for t in current_tags if t not in drop]
            updated_image = {**current, "tags": next_tags}
            await write_json(file, updated_image)
            return True

        ok = await with_file_lock(file, fn)
        if ok:
            updated += 1
    return {"updated": updated}


async def delete_image(project_id: str, image_id: str) -> None:
    """Cascade: drop annotations referencing this image from every LabelSet,
    drop the image id from every LabelSet's membership list, then remove
    from the project image index and the directory itself.
    """
    from .labelsets import (
        mutate_labelset,
        mutate_labelset_annotations,
    )

    labelset_ids = await _read_index_list(labelsets_index(project_id))
    for lsid in labelset_ids:
        async def drop_anns(
            data: dict[str, Any], _iid: str = image_id
        ) -> bool:
            before = len(data["annotations"])
            data["annotations"] = [
                a for a in data["annotations"] if a["imageId"] != _iid
            ]
            # `False` skips the write; only persist when something actually changed.
            return len(data["annotations"]) != before

        await mutate_labelset_annotations(project_id, lsid, drop_anns)

        def drop_membership(
            ls: dict[str, Any], _iid: str = image_id
        ) -> bool | None:
            if _iid not in ls.get("imageIds", []):
                return False
            ls["imageIds"] = [i for i in ls["imageIds"] if i != _iid]
            return None

        await mutate_labelset(project_id, lsid, drop_membership)

    await _remove_index_id(images_index(project_id), image_id)
    await asyncio.to_thread(
        shutil.rmtree, image_dir(project_id, image_id), ignore_errors=True
    )


async def read_image_bytes(
    project_id: str, image_id: str
) -> tuple[bytes, str] | None:
    img = await get_image(project_id, image_id)
    if img is None:
        return None
    p = image_dir(project_id, image_id) / f"bytes.{img['ext']}"
    data = await read_bytes(p)
    if data is None:
        return None
    return data, img["ext"]


async def read_image_thumb(project_id: str, image_id: str) -> bytes | None:
    """Lazy 384px JPEG thumbnail.

    Reads the cached file if present; otherwise decodes the original,
    downscales with Pillow, writes the result, and returns it. Concurrent
    first-access callers for the same image are serialized on the
    thumb path lock so the encode never duplicates.
    """
    tp = _thumb_path(project_id, image_id)
    cached = await read_bytes(tp)
    if cached is not None:
        return cached

    async def fn() -> bytes | None:
        # Re-check under the lock — a parallel waiter may have written it.
        cached = await read_bytes(tp)
        if cached is not None:
            return cached
        src = await read_image_bytes(project_id, image_id)
        if src is None:
            return None
        out = await asyncio.to_thread(_render_thumb, src[0])
        await write_bytes_atomic(tp, out)
        return out

    return await with_file_lock(tp, fn)


def _render_thumb(data: bytes) -> bytes:
    with PILImage.open(io.BytesIO(data)) as raw:
        # Apply EXIF orientation so portrait phone photos are not sideways.
        img = ImageOps.exif_transpose(raw) or raw
        img.thumbnail((_THUMB_MAX, _THUMB_MAX), PILImage.Resampling.LANCZOS)
        if img.mode != "RGB":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=75, optimize=True)
        return buf.getvalue()
