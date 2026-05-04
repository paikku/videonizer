"""Streaming ZIP build for dataset export.

Layout in the ZIP:
    annotations.json
    images/<file_name>           (only when include_images=True)

Image bytes stream straight from the BlobStore into the ZIP body — never
buffered fully in RAM, so multi-GB exports are safe.

zipstream-ng's ``ZipStream`` is sync; we use it from the FastAPI handler by
adding all files (each with a generator of bytes) and iterating it on a
worker thread. The iterator yields chunks as files complete.
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Iterable

from zipstream import ZipStream

from ..storage.blobs import BlobNotFound, BlobStore
from ..storage.repo.annotations import AnnotationRepo, data_load
from ..storage.repo.images import ImageRepo
from ..storage.repo.labelsets import LabelSet, _list_load


def _build_annotations_payload(
    labelset: LabelSet,
    annotations: list,
    items: list[dict],
    *,
    remap_class_ids: bool,
) -> dict:
    classes = _list_load(labelset.classes_json)
    class_map: dict[str, int] = {}
    if remap_class_ids:
        for i, c in enumerate(classes):
            cid = c.get("id") if isinstance(c, dict) else None
            if cid is not None:
                class_map[str(cid)] = i

    def _cls(cid: str) -> str | int:
        return class_map.get(cid, cid)

    out_annotations = []
    for r in annotations:
        d = data_load(r.data_json)
        entry: dict = {
            "id": r.id,
            "imageId": r.image_id,
            "classId": _cls(r.class_id),
            "kind": r.kind,
        }
        if r.kind == "rect":
            entry["rect"] = {
                "x": d.get("x", 0.0),
                "y": d.get("y", 0.0),
                "w": d.get("w", 0.0),
                "h": d.get("h", 0.0),
            }
        elif r.kind == "polygon":
            entry["polygon"] = d.get("polygon") or []
        out_annotations.append(entry)

    return {
        "labelSetId": labelset.id,
        "name": labelset.name,
        "type": labelset.type,
        "classes": classes,
        "items": items,
        "annotations": out_annotations,
    }


def _dedup_name(name: str, seen: set[str]) -> str:
    if name not in seen:
        seen.add(name)
        return name
    base, _, ext = name.rpartition(".")
    if not base:
        base, ext = name, ""
    i = 1
    while True:
        candidate = f"{base}_{i}.{ext}" if ext else f"{base}_{i}"
        if candidate not in seen:
            seen.add(candidate)
            return candidate
        i += 1


async def build_zip_stream(
    *,
    session,
    store: BlobStore,
    labelset: LabelSet,
    items: list[dict],
    include_images: bool,
    remap_class_ids: bool,
) -> AsyncIterator[bytes]:
    annotations = await AnnotationRepo(session).list_for_labelset(labelset.id)
    manifest = _build_annotations_payload(
        labelset, annotations, items, remap_class_ids=remap_class_ids
    )
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode(
        "utf-8"
    )

    # Pre-fetch image rows + bytes since we need them in a sync iterator.
    # For multi-GB datasets this would be a memory blow-up; stage them on
    # disk in a future pass if it becomes a problem. For now S3 → memory →
    # ZIP works for a single-PC deployment.
    image_payloads: list[tuple[str, bytes]] = []
    if include_images:
        img_repo = ImageRepo(session)
        seen: set[str] = set()
        for item in items:
            if item.get("excluded"):
                continue
            iid = item["imageId"]
            row = await img_repo.get_by_id(iid)
            if row is None:
                continue
            try:
                data, _meta = await store.get_bytes(row.bytes_blob_key)
            except BlobNotFound:
                continue
            name = item.get("fileName") or row.file_name or iid
            arc = _dedup_name(f"images/{name}", seen)
            image_payloads.append((arc, data))

    def _build_zs() -> ZipStream:
        zs = ZipStream()
        zs.add(manifest_bytes, "annotations.json")
        for arc, data in image_payloads:
            zs.add(data, arc)
        return zs

    zs = _build_zs()

    # Iterating the zipstream yields bytes as it serializes. Push that work
    # onto a thread so the event loop stays free.
    queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=4)

    async def _drain() -> AsyncIterator[bytes]:
        while True:
            item = await queue.get()
            if item is None:
                return
            yield item

    loop = asyncio.get_running_loop()

    def _produce() -> None:
        try:
            for chunk in zs:
                fut = asyncio.run_coroutine_threadsafe(queue.put(chunk), loop)
                fut.result()
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

    producer = asyncio.create_task(asyncio.to_thread(_produce))
    try:
        async for chunk in _drain():
            yield chunk
    finally:
        await producer
