"""Resources API — video / image_batch upload, source streaming, previews,
image ingest. Thin layer over ``app.storage``.

Most upload paths buffer the whole body into memory (for parity with
vision's Next.js routes which do the same). Source streaming is the
exception: it serves directly off disk so HTTP byte-range responses can
work without RAM pressure on multi-GB videos.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, File, Form, Header, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from .. import storage
from ..errors import (
    BadRequestError,
    NotFoundError,
    ServiceError,
    UploadTooLarge,
)

router = APIRouter(prefix="/v1/projects/{project_id}/resources", tags=["resources"])

UPLOAD_CHUNK = 1024 * 1024  # 1 MiB
RANGE_CHUNK = 64 * 1024  # 64 KiB per yield in the Range streamer


async def _read_upload_bounded(file: UploadFile, limit: int) -> bytes:
    """Buffer an upload into memory while enforcing ``limit``.

    For the routes that serve uploads small enough to fit in RAM (preview
    JPEGs, single-frame images). Source video uploads use the same
    pattern — vision does too — but the practical cap is
    ``Settings.max_upload_bytes`` (default 2 GiB).
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(UPLOAD_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise UploadTooLarge(limit)
        chunks.append(chunk)
    return b"".join(chunks)


# ----- list / create ---------------------------------------------------


class UpdateResourceBody(BaseModel):
    name: str | None = None
    tags: list[str] | None = None


@router.get("")
async def list_resources(project_id: str) -> dict:
    return {"resources": await storage.list_resources(project_id)}


@router.post("", status_code=201)
async def create_resource(
    project_id: str,
    request: Request,
    type: str = Form(...),
    name: str = Form(...),
    tags: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    width: int | None = Form(default=None),
    height: int | None = Form(default=None),
    duration: float | None = Form(default=None),
    ingestVia: str | None = Form(default=None),
) -> dict:
    name_clean = (name or "").strip()
    if not name_clean:
        raise BadRequestError("name is required")

    parsed_tags: list[str] = []
    if tags:
        try:
            parsed = json.loads(tags)
        except Exception as exc:
            raise BadRequestError("tags must be a JSON array") from exc
        if not isinstance(parsed, list):
            raise BadRequestError("tags must be a JSON array")
        parsed_tags = [str(t) for t in parsed]

    if type == "image_batch":
        r = await storage.create_resource(
            project_id, type="image_batch", name=name_clean, tags=parsed_tags
        )
        return {"resource": r}

    if type == "video":
        if file is None or not file.filename:
            raise BadRequestError("file is required")
        if width is None or height is None:
            raise BadRequestError("width/height required")
        settings = request.app.state.settings
        try:
            buf = await _read_upload_bounded(file, settings.max_upload_bytes)
        finally:
            await file.close()
        ext = storage.ext_from_name(file.filename, "mp4")
        # ``ingestVia`` is the FE-supplied source-of-bytes hint; storage
        # accepts None so the absence stays absent.
        r = await storage.create_resource(
            project_id,
            type="video",
            name=name_clean,
            tags=parsed_tags,
            source_ext=ext,
            source_buffer=buf,
            width=int(width),
            height=int(height),
            duration=float(duration) if duration is not None else None,
            ingest_via=ingestVia,
        )
        return {"resource": r}

    raise BadRequestError("type must be video | image_batch")


# ----- get / patch / delete --------------------------------------------


@router.get("/{rid}")
async def get_resource(project_id: str, rid: str) -> dict:
    r = await storage.get_resource(project_id, rid)
    if r is None:
        raise NotFoundError(f"resource {rid} not found")
    return {"resource": r}


@router.patch("/{rid}")
async def update_resource(project_id: str, rid: str, body: UpdateResourceBody) -> dict:
    r = await storage.update_resource(
        project_id, rid, name=body.name, tags=body.tags
    )
    if r is None:
        raise NotFoundError(f"resource {rid} not found")
    return {"resource": r}


@router.delete("/{rid}")
async def delete_resource(project_id: str, rid: str) -> dict:
    """Cascade-deletes the resource, its source bytes/previews, and every
    Image whose ``resourceId`` matches (which in turn strips those images
    from labelset memberships and any annotations against them).
    Idempotent.
    """
    await storage.delete_resource(project_id, rid)
    return {"ok": True}


# ----- source streaming ------------------------------------------------


_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _read_range(path: Path, start: int, length: int) -> AsyncIterator[bytes]:
    """Yield ``length`` bytes from ``path`` starting at offset ``start``.

    Wrapped as an async generator so FastAPI's ``StreamingResponse`` can
    pump it. The underlying file IO is sync — fine here since the
    server uses the threaded worker pool for blocking IO and the chunks
    are small (64 KiB).
    """

    async def gen() -> AsyncIterator[bytes]:
        with path.open("rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(RANGE_CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return gen()


@router.get("/{rid}/source")
async def get_resource_source(
    project_id: str,
    rid: str,
    range: str | None = Header(default=None),
) -> Response:
    """Serve the source video with HTTP byte-range support.

    Browsers REQUIRE 206 responses to seek inside ``<video>``; without
    them clicking the scrubber and any scripted ``currentTime`` write
    fail silently. We parse the Range header, return 206 Partial Content
    for the requested window, and 200 (with ``Accept-Ranges: bytes``)
    for unranged requests.
    """
    stat = await storage.stat_resource_source(project_id, rid)
    if stat is None:
        raise NotFoundError(f"resource {rid} source not found")
    mime = storage.mime_for_ext(stat["ext"])
    total: int = stat["size"]
    path: Path = stat["path"]

    cache_headers = {
        "accept-ranges": "bytes",
        "cache-control": "private, max-age=0, must-revalidate",
    }

    if range is None:
        return StreamingResponse(
            _read_range(path, 0, total),
            status_code=200,
            media_type=mime,
            headers={"content-length": str(total), **cache_headers},
        )

    m = _RANGE_RE.match(range.strip())
    if not m:
        return Response(
            content="invalid range",
            status_code=416,
            headers={"content-range": f"bytes */{total}"},
        )
    start_str, end_str = m.group(1), m.group(2)
    if start_str == "" and end_str != "":
        # Suffix range: last N bytes.
        try:
            n = int(end_str)
        except ValueError:
            n = 0
        if n <= 0:
            return Response(
                content="invalid range",
                status_code=416,
                headers={"content-range": f"bytes */{total}"},
            )
        start = max(0, total - n)
        end = total - 1
    else:
        try:
            start = 0 if start_str == "" else int(start_str)
            end = total - 1 if end_str == "" else int(end_str)
        except ValueError:
            return Response(
                content="invalid range",
                status_code=416,
                headers={"content-range": f"bytes */{total}"},
            )

    if start < 0 or end >= total or start > end:
        return Response(
            content="invalid range",
            status_code=416,
            headers={"content-range": f"bytes */{total}"},
        )

    chunk_size = end - start + 1
    return StreamingResponse(
        _read_range(path, start, chunk_size),
        status_code=206,
        media_type=mime,
        headers={
            "content-length": str(chunk_size),
            "content-range": f"bytes {start}-{end}/{total}",
            **cache_headers,
        },
    )


# ----- previews --------------------------------------------------------


@router.post("/{rid}/previews")
async def write_previews(
    project_id: str,
    rid: str,
    request: Request,
    files: list[UploadFile] = File(...),
) -> dict:
    """Replace the preview reel for a video resource. Wipes prior frames."""
    settings = request.app.state.settings
    buffers: list[bytes] = []
    for f in files:
        try:
            buf = await _read_upload_bounded(f, settings.max_upload_bytes)
        finally:
            await f.close()
        buffers.append(buf)
    try:
        n = await storage.write_previews(project_id, rid, buffers)
    except FileNotFoundError as exc:
        raise NotFoundError(str(exc)) from exc
    except ValueError as exc:
        # 422 per contract — the resource exists but is the wrong type.
        raise ServiceError("invalid_input", str(exc), 422) from exc
    return {"previewCount": n}


@router.get("/{rid}/previews/{idx}")
async def get_preview(project_id: str, rid: str, idx: str) -> Response:
    try:
        n = int(idx)
    except ValueError as exc:
        raise BadRequestError("invalid index") from exc
    data = await storage.read_preview(project_id, rid, n)
    if data is None:
        raise NotFoundError(f"preview {idx} not found")
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"cache-control": "private, max-age=31536000, immutable"},
    )


# ----- image ingest ----------------------------------------------------


@router.post("/{rid}/images", status_code=201)
async def add_images_to_resource(
    project_id: str,
    rid: str,
    request: Request,
    meta: str = Form(...),
    files: list[UploadFile] = File(...),
) -> dict:
    """Add Image rows to a Resource.

    ``meta`` is a JSON-encoded array, one entry per file in ``files``,
    same order. Image ``source`` is derived from the parent resource
    type — video resources produce ``video_frame`` images, image_batch
    resources produce ``uploaded`` images.
    """
    resource = await storage.get_resource(project_id, rid)
    if resource is None:
        raise NotFoundError(f"resource {rid} not found")

    try:
        meta_arr = json.loads(meta)
    except Exception as exc:
        raise BadRequestError("invalid meta") from exc
    if not isinstance(meta_arr, list):
        raise BadRequestError("meta must be an array")
    if len(meta_arr) != len(files):
        raise BadRequestError(
            f"meta count ({len(meta_arr)}) != file count ({len(files)})"
        )

    settings = request.app.state.settings
    src_kind = "video_frame" if resource["type"] == "video" else "uploaded"

    out: list[dict[str, Any]] = []
    for f, m in zip(files, meta_arr):
        if not isinstance(m, dict):
            raise BadRequestError("meta entry must be an object")
        file_name = (m.get("fileName") or f.filename or "").strip()
        if not file_name:
            raise BadRequestError("meta.fileName is required")
        width = m.get("width")
        height = m.get("height")
        if not isinstance(width, (int, float)) or not isinstance(height, (int, float)):
            raise BadRequestError("meta.width and meta.height are required numbers")
        ext = storage.ext_from_name(file_name, "jpg")
        try:
            buf = await _read_upload_bounded(f, settings.max_upload_bytes)
        finally:
            await f.close()
        video_frame_meta: dict[str, Any] | None = None
        if src_kind == "video_frame" and isinstance(m.get("timestamp"), (int, float)):
            video_frame_meta = {"timestamp": float(m["timestamp"])}
            if isinstance(m.get("frameIndex"), int):
                video_frame_meta["frameIndex"] = m["frameIndex"]
        try:
            image = await storage.create_image(
                project_id,
                resource_id=rid,
                source=src_kind,
                file_name=file_name,
                ext=ext,
                width=int(width),
                height=int(height),
                bytes_=buf,
                video_frame_meta=video_frame_meta,
                image_id=m.get("id"),
            )
        except ValueError as exc:
            # Bad client-allocated id (path traversal, empty, ...)
            raise BadRequestError(str(exc)) from exc
        out.append(image)
    return {"images": out}
