"""Image routes — /api/projects/{pid}/images.

Single CRUD + bulk delete + bulk tag + bytes/thumb serving. Image
ingestion (the multipart `files[] + meta` payload) is wired into the
existing ``POST /resources/{rid}/images`` route in this PR (see
``app/api/resources.py`` patch).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain.images import (
    BulkDeleteImagesRequest,
    BulkDeleteImagesResponse,
    BulkTagsRequest,
    BulkTagsResponse,
    Image as ImageDTO,
    ImageListResponse,
    ImageResponse,
    ImageUpdate,
)
from ..domain.projects import OkResponse
from ..errors import BadRequest, NotFound
from ..services.blob_keys import image_prefix
from ..storage.blobs import BlobNotFound, BlobStore
from ..storage.repo.images import Image as ImageRow, ImageRepo, _tags_load
from ..storage.repo.projects import ProjectRepo
from ._deps import current_user_id, get_session, get_store

router = APIRouter(prefix="/api/projects/{project_id}/images", tags=["images"])

_VALID_SOURCES = {"uploaded", "video_frame"}
_VALID_TAG_MODES = {"add", "replace", "remove"}


def _to_dto(row: ImageRow) -> ImageDTO:
    return ImageDTO(
        id=row.id,
        project_id=row.project_id,
        resource_id=row.resource_id,
        source=row.source,  # type: ignore[arg-type]
        file_name=row.file_name,
        width=row.width,
        height=row.height,
        timestamp=row.timestamp,
        frame_index=row.frame_index,
        tags=_tags_load(row.tags_json),
        created_at=row.created_at,
    )


async def _require_project(session: AsyncSession, project_id: str) -> None:
    if await ProjectRepo(session).get(project_id) is None:
        raise NotFound("project_not_found", f"project {project_id} not found")


async def _require_image(
    session: AsyncSession, project_id: str, image_id: str
) -> ImageRow:
    await _require_project(session, project_id)
    row = await ImageRepo(session).get(project_id, image_id)
    if row is None:
        raise NotFound("image_not_found", f"image {image_id} not found")
    return row


# --- list / get / patch / delete ------------------------------------------


@router.get("", response_model=ImageListResponse)
async def list_images(
    project_id: str,
    resource_id: str | None = Query(default=None, alias="resourceId"),
    source: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    _user: str = Depends(current_user_id),
) -> ImageListResponse:
    await _require_project(session, project_id)
    if source is not None and source not in _VALID_SOURCES:
        raise BadRequest(
            f"source must be one of {sorted(_VALID_SOURCES)}",
            code="invalid_source",
        )
    rows = await ImageRepo(session).list_for_project(
        project_id, resource_id=resource_id, source=source, tag=tag
    )
    return ImageListResponse(images=[_to_dto(r) for r in rows])


@router.get("/{image_id}", response_model=ImageResponse)
async def get_image(
    project_id: str,
    image_id: str,
    session: AsyncSession = Depends(get_session),
    _user: str = Depends(current_user_id),
) -> ImageResponse:
    row = await _require_image(session, project_id, image_id)
    return ImageResponse(image=_to_dto(row))


@router.patch("/{image_id}", response_model=ImageResponse)
async def patch_image(
    project_id: str,
    image_id: str,
    body: ImageUpdate,
    session: AsyncSession = Depends(get_session),
    _user: str = Depends(current_user_id),
) -> ImageResponse:
    row = await _require_image(session, project_id, image_id)
    if body.tags is not None:
        row = await ImageRepo(session).update_tags(row, body.tags)
    return ImageResponse(image=_to_dto(row))


@router.delete("/{image_id}", response_model=OkResponse)
async def delete_image(
    project_id: str,
    image_id: str,
    session: AsyncSession = Depends(get_session),
    store: BlobStore = Depends(get_store),
    _user: str = Depends(current_user_id),
) -> OkResponse:
    row = await ImageRepo(session).delete(project_id, image_id)
    if row is None:
        await _require_project(session, project_id)
        raise NotFound("image_not_found", f"image {image_id} not found")
    await store.delete_prefix(image_prefix(project_id, image_id))
    return OkResponse(ok=True)


# --- bulk delete + bulk tag -----------------------------------------------


@router.post("/delete", response_model=BulkDeleteImagesResponse)
async def bulk_delete_images(
    project_id: str,
    body: BulkDeleteImagesRequest,
    session: AsyncSession = Depends(get_session),
    store: BlobStore = Depends(get_store),
    _user: str = Depends(current_user_id),
) -> BulkDeleteImagesResponse:
    await _require_project(session, project_id)
    if not body.image_ids:
        return BulkDeleteImagesResponse(deleted=0)
    rows = await ImageRepo(session).bulk_delete(project_id, body.image_ids)
    for r in rows:
        await store.delete_prefix(image_prefix(project_id, r.id))
    return BulkDeleteImagesResponse(deleted=len(rows))


@router.post("/tags", response_model=BulkTagsResponse)
async def bulk_tag_images(
    project_id: str,
    body: BulkTagsRequest,
    session: AsyncSession = Depends(get_session),
    _user: str = Depends(current_user_id),
) -> BulkTagsResponse:
    await _require_project(session, project_id)
    if body.mode not in _VALID_TAG_MODES:
        raise BadRequest(
            f"mode must be one of {sorted(_VALID_TAG_MODES)}", code="invalid_mode"
        )
    if not body.image_ids:
        return BulkTagsResponse(updated=0)
    updated = await ImageRepo(session).bulk_tags(
        project_id, body.image_ids, tags=body.tags, mode=body.mode
    )
    return BulkTagsResponse(updated=updated)


# --- bytes / thumb --------------------------------------------------------


_IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}


@router.get("/{image_id}/bytes")
async def get_bytes(
    project_id: str,
    image_id: str,
    session: AsyncSession = Depends(get_session),
    store: BlobStore = Depends(get_store),
    _user: str = Depends(current_user_id),
) -> Response:
    row = await _require_image(session, project_id, image_id)
    try:
        data, meta = await store.get_bytes(row.bytes_blob_key)
    except BlobNotFound as exc:
        raise NotFound("image_bytes_missing", "image bytes missing") from exc
    return Response(
        content=data,
        media_type=meta.content_type or row.bytes_content_type,
        headers=_IMMUTABLE,
    )


@router.get("/{image_id}/thumb")
async def get_thumb(
    project_id: str,
    image_id: str,
    session: AsyncSession = Depends(get_session),
    store: BlobStore = Depends(get_store),
    _user: str = Depends(current_user_id),
) -> Response:
    row = await _require_image(session, project_id, image_id)
    try:
        data, meta = await store.get_bytes(row.thumb_blob_key)
    except BlobNotFound as exc:
        raise NotFound("image_thumb_missing", "image thumb missing") from exc
    return Response(
        content=data,
        media_type=meta.content_type or "image/jpeg",
        headers=_IMMUTABLE,
    )
