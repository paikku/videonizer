"""Images API — list/get/patch/delete + raw bytes/thumbnail + bulk tag.

Image creation lives on the Resources router (``POST .../resources/{rid}/images``)
because every Image originates from a Resource. This module only handles
post-ingest operations.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from .. import storage
from ..errors import BadRequestError, NotFoundError

router = APIRouter(prefix="/v1/projects/{project_id}/images", tags=["images"])


# Long-lived caching is safe because image bytes / thumbs are content-addressed
# by the immutable image id; mutating tags doesn't change the binary.
_IMMUTABLE = "private, max-age=31536000, immutable"


class UpdateImageBody(BaseModel):
    tags: list[str] | None = None


class BulkTagBody(BaseModel):
    imageIds: list[str] | None = None
    tags: list[str] | None = None
    mode: str | None = None  # "add" | "remove" | "replace"


# ----- list / item CRUD ------------------------------------------------


@router.get("")
async def list_images(
    project_id: str,
    resourceId: str | None = Query(default=None),
    source: str | None = Query(default=None),
    tag: str | None = Query(default=None),
) -> dict:
    images = await storage.list_images(
        project_id, resource_id=resourceId, source=source, tag=tag
    )
    return {"images": images}


@router.get("/{iid}")
async def get_image(project_id: str, iid: str) -> dict:
    img = await storage.get_image(project_id, iid)
    if img is None:
        raise NotFoundError(f"image {iid} not found")
    return {"image": img}


@router.patch("/{iid}")
async def update_image(project_id: str, iid: str, body: UpdateImageBody) -> dict:
    img = await storage.update_image(project_id, iid, tags=body.tags)
    if img is None:
        raise NotFoundError(f"image {iid} not found")
    return {"image": img}


@router.delete("/{iid}")
async def delete_image(project_id: str, iid: str) -> dict:
    """Cascade-deletes the image, its bytes/thumb, and removes it from
    every LabelSet's ``imageIds`` and any annotation whose ``imageId``
    matches. Idempotent.
    """
    await storage.delete_image(project_id, iid)
    return {"ok": True}


# ----- bytes / thumb (binary endpoints, non-/{iid} sibling routes) ----


@router.get("/{iid}/bytes")
async def get_image_bytes(project_id: str, iid: str) -> Response:
    res = await storage.read_image_bytes(project_id, iid)
    if res is None:
        raise NotFoundError(f"image {iid} not found")
    data, ext = res
    return Response(
        content=data,
        media_type=storage.mime_for_ext(ext),
        headers={"cache-control": _IMMUTABLE},
    )


@router.get("/{iid}/thumb")
async def get_image_thumb(project_id: str, iid: str) -> Response:
    data = await storage.read_image_thumb(project_id, iid)
    if data is None:
        raise NotFoundError(f"image {iid} not found")
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"cache-control": _IMMUTABLE},
    )


# ----- bulk tag --------------------------------------------------------


@router.post("/tags")
async def bulk_tag(project_id: str, body: BulkTagBody) -> dict:
    """Bulk tag mutation across many images. ``mode``:

    * ``add`` (default) — union with existing tags.
    * ``remove`` — subtract.
    * ``replace`` — overwrite.

    Empty ``imageIds`` returns ``{"updated": 0}`` without touching
    anything; vision's TS uses the same shortcut.
    """
    image_ids = list(body.imageIds or [])
    tags_in = list(body.tags or [])
    # Match vision: trim and drop empties.
    tags = [t.strip() for t in tags_in if isinstance(t, str)]
    tags = [t for t in tags if t]
    mode = body.mode if body.mode in ("replace", "remove") else "add"
    if not image_ids:
        return {"updated": 0}
    out = await storage.bulk_tag_images(project_id, image_ids, tags, mode)
    return out
