"""Resource routes — /api/projects/{pid}/resources.

Covers: list, create (video / image_batch), get, patch, delete (single +
bulk), original-source serving with HTTP Range, preview tiles upload and
serving. Image batch ingestion lives in PR #4 (POST /resources/{rid}/images
returns 501 Not Implemented for now).
"""
from __future__ import annotations

import json
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    Path as PathParam,
    UploadFile,
    status,
)
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..domain.projects import OkResponse
from ..domain.resources import (
    BulkDeleteRequest,
    BulkDeleteResponse,
    PreviewUploadResponse,
    Resource as ResourceDTO,
    ResourceListResponse,
    ResourceResponse,
    ResourceSummary,
    ResourceUpdate,
)
from ..errors import BadRequest, NotFound, ValidationError
from ..services.blob_keys import (
    resource_preview_key,
    resource_prefix,
    resource_source_key,
    safe_extension,
)
from ..services.uploads import stream_upload_to_blob, buffer_to_memory
from ..storage.blobs import BlobNotFound, BlobStore
from ..storage.repo.projects import ProjectRepo
from ..storage.repo.resources import (
    Resource as ResourceRow,
    ResourceRepo,
    _tags_load,
)
from ._deps import current_user_id, get_session, get_store
from ._range import (
    InvalidRange,
    parse_range,
    range_not_satisfiable,
    stream_full,
    stream_range,
)

router = APIRouter(prefix="/api/projects/{project_id}/resources", tags=["resources"])


_ALLOWED_TYPES = {"video", "image_batch"}
_ALLOWED_INGEST = {"client", "server", None}


def _to_dto(row: ResourceRow) -> ResourceDTO:
    return ResourceDTO(
        id=row.id,
        project_id=row.project_id,
        type=row.type,  # type: ignore[arg-type]
        name=row.name,
        tags=_tags_load(row.tags_json),
        width=row.width,
        height=row.height,
        duration=row.duration,
        ingest_via=row.ingest_via,  # type: ignore[arg-type]
        has_source=row.source_blob_key is not None,
        preview_count=row.preview_count,
        created_at=row.created_at,
    )


def _to_summary(row: ResourceRow, image_count: int = 0) -> ResourceSummary:
    return ResourceSummary(
        **_to_dto(row).model_dump(by_alias=False),
        image_count=image_count,
    )


async def _require_project(
    session: AsyncSession, project_id: str
) -> None:
    row = await ProjectRepo(session).get(project_id)
    if row is None:
        raise NotFound("project_not_found", f"project {project_id} not found")


async def _require_resource(
    session: AsyncSession, project_id: str, resource_id: str
) -> ResourceRow:
    await _require_project(session, project_id)
    row = await ResourceRepo(session).get(project_id, resource_id)
    if row is None:
        raise NotFound("resource_not_found", f"resource {resource_id} not found")
    return row


# --- list ------------------------------------------------------------------


@router.get("", response_model=ResourceListResponse)
async def list_resources(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    _user: str = Depends(current_user_id),
) -> ResourceListResponse:
    await _require_project(session, project_id)
    rows = await ResourceRepo(session).list_for_project(project_id)
    return ResourceListResponse(resources=[_to_summary(r) for r in rows])


# --- create ----------------------------------------------------------------


def _parse_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        v = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BadRequest(f"invalid tags JSON: {exc}", code="invalid_tags") from exc
    if not isinstance(v, list):
        raise BadRequest("tags must be a JSON array of strings", code="invalid_tags")
    return [str(x) for x in v]


@router.post(
    "",
    response_model=ResourceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_resource(
    project_id: str,
    type: Annotated[str, Form()],
    name: Annotated[str, Form()],
    tags: Annotated[str | None, Form()] = None,
    width: Annotated[int | None, Form()] = None,
    height: Annotated[int | None, Form()] = None,
    duration: Annotated[float | None, Form()] = None,
    ingest_via: Annotated[str | None, Form(alias="ingestVia")] = None,
    file: Annotated[UploadFile | None, File()] = None,
    session: AsyncSession = Depends(get_session),
    store: BlobStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
    _user: str = Depends(current_user_id),
) -> ResourceResponse:
    if type not in _ALLOWED_TYPES:
        raise BadRequest(
            f"type must be one of {sorted(_ALLOWED_TYPES)}",
            code="invalid_type",
        )
    if ingest_via not in _ALLOWED_INGEST:
        raise BadRequest("invalid ingestVia", code="invalid_ingest_via")
    if not name.strip():
        raise ValidationError("name must not be empty")
    parsed_tags = _parse_tags(tags)

    await _require_project(session, project_id)

    if type == "video":
        if file is None:
            raise BadRequest("type=video requires file", code="missing_file")
        if width is None or height is None:
            raise BadRequest(
                "type=video requires width and height", code="missing_dimensions"
            )

    repo = ResourceRepo(session)
    row = await repo.create(
        project_id=project_id,
        type_=type,
        name=name.strip(),
        tags=parsed_tags,
        width=width,
        height=height,
        duration=duration,
        ingest_via=ingest_via,
    )

    if type == "video" and file is not None:
        ext = safe_extension(file.filename) or "bin"
        key = resource_source_key(project_id, row.id, ext)
        meta = await stream_upload_to_blob(
            file,
            store=store,
            key=key,
            content_type=file.content_type or "application/octet-stream",
            limit=settings.max_upload_bytes,
            temp_dir=settings.temp_dir or None,
        )
        await repo.set_source(
            row,
            key=key,
            size=meta.size,
            content_type=meta.content_type,
        )

    return ResourceResponse(resource=_to_dto(row))


# --- single get / patch / delete ------------------------------------------


@router.get("/{resource_id}", response_model=ResourceResponse)
async def get_resource(
    project_id: str,
    resource_id: str,
    session: AsyncSession = Depends(get_session),
    _user: str = Depends(current_user_id),
) -> ResourceResponse:
    row = await _require_resource(session, project_id, resource_id)
    return ResourceResponse(resource=_to_dto(row))


@router.patch("/{resource_id}", response_model=ResourceResponse)
async def patch_resource(
    project_id: str,
    resource_id: str,
    body: ResourceUpdate,
    session: AsyncSession = Depends(get_session),
    _user: str = Depends(current_user_id),
) -> ResourceResponse:
    row = await _require_resource(session, project_id, resource_id)
    name = body.name.strip() if body.name is not None else None
    if name == "":
        raise ValidationError("name must not be empty")
    row = await ResourceRepo(session).update(row, name=name, tags=body.tags)
    return ResourceResponse(resource=_to_dto(row))


@router.delete("/{resource_id}", response_model=OkResponse)
async def delete_resource(
    project_id: str,
    resource_id: str,
    session: AsyncSession = Depends(get_session),
    store: BlobStore = Depends(get_store),
    _user: str = Depends(current_user_id),
) -> OkResponse:
    row = await ResourceRepo(session).delete(project_id, resource_id)
    if row is None:
        # Distinguish "no project" from "no resource" so the frontend can
        # render the right toast.
        await _require_project(session, project_id)
        raise NotFound("resource_not_found", f"resource {resource_id} not found")
    # Cascade blobs (source + previews + future image bytes).
    await store.delete_prefix(resource_prefix(project_id, resource_id))
    return OkResponse(ok=True)


# --- bulk delete -----------------------------------------------------------


@router.post("/delete", response_model=BulkDeleteResponse)
async def bulk_delete_resources(
    project_id: str,
    body: BulkDeleteRequest,
    session: AsyncSession = Depends(get_session),
    store: BlobStore = Depends(get_store),
    _user: str = Depends(current_user_id),
) -> BulkDeleteResponse:
    await _require_project(session, project_id)
    if not body.resource_ids:
        return BulkDeleteResponse(deleted=0)
    rows = await ResourceRepo(session).bulk_delete(project_id, body.resource_ids)
    for r in rows:
        await store.delete_prefix(resource_prefix(project_id, r.id))
    return BulkDeleteResponse(deleted=len(rows))


# --- source bytes (Range) --------------------------------------------------


@router.get("/{resource_id}/source")
async def get_source(
    project_id: str,
    resource_id: str,
    range_header: Annotated[str | None, Header(alias="range")] = None,
    session: AsyncSession = Depends(get_session),
    store: BlobStore = Depends(get_store),
    _user: str = Depends(current_user_id),
) -> Response:
    row = await _require_resource(session, project_id, resource_id)
    if not row.source_blob_key:
        raise NotFound("source_not_available", "resource has no source bytes")

    media_type = row.source_content_type or "application/octet-stream"
    head = await store.head(row.source_blob_key)
    total = head.size

    try:
        rng = parse_range(range_header, total)
    except InvalidRange:
        return range_not_satisfiable(total)

    if rng is None:
        body, _meta, _total = await store.get_range(row.source_blob_key, 0, total - 1)
        return stream_full(
            body,
            total=total,
            media_type=media_type,
            extra_headers={"Cache-Control": "private, max-age=0"},
        )

    start, end = rng
    body, _meta, _total = await store.get_range(row.source_blob_key, start, end)
    return stream_range(
        body,
        start=start,
        end=end,
        total=total,
        media_type=media_type,
        extra_headers={"Cache-Control": "private, max-age=0"},
    )


# --- previews --------------------------------------------------------------


@router.post(
    "/{resource_id}/previews",
    response_model=PreviewUploadResponse,
)
async def upload_previews(
    project_id: str,
    resource_id: str,
    files: Annotated[list[UploadFile], File()],
    session: AsyncSession = Depends(get_session),
    store: BlobStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
    _user: str = Depends(current_user_id),
) -> PreviewUploadResponse:
    row = await _require_resource(session, project_id, resource_id)
    if not files:
        raise BadRequest("no preview files provided", code="empty_files")

    # Replace any existing previews to avoid orphaned tiles after a re-upload.
    await store.delete_prefix(resource_prefix(project_id, resource_id) + "previews/")

    for idx, upload in enumerate(files):
        data = await buffer_to_memory(upload, limit=settings.segment_max_upload_bytes)
        key = resource_preview_key(project_id, resource_id, idx)
        await store.put_bytes(key, data, "image/jpeg")

    repo = ResourceRepo(session)
    row = await repo.set_preview_count(row, len(files))
    return PreviewUploadResponse(preview_count=row.preview_count)


@router.get("/{resource_id}/previews/{idx}")
async def get_preview(
    project_id: str,
    resource_id: str,
    idx: int = PathParam(..., ge=0),
    session: AsyncSession = Depends(get_session),
    store: BlobStore = Depends(get_store),
    _user: str = Depends(current_user_id),
) -> Response:
    row = await _require_resource(session, project_id, resource_id)
    if idx >= row.preview_count:
        raise NotFound("preview_not_found", f"preview {idx} not found")

    key = resource_preview_key(project_id, resource_id, idx)
    try:
        data, meta = await store.get_bytes(key)
    except BlobNotFound as exc:
        raise NotFound("preview_not_found", f"preview {idx} not found") from exc

    return Response(
        content=data,
        media_type=meta.content_type or "image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


# --- image ingest stub (PR #4 fills in) ------------------------------------


@router.post("/{resource_id}/images", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def add_images_stub(
    project_id: str,
    resource_id: str,
    session: AsyncSession = Depends(get_session),
    _user: str = Depends(current_user_id),
) -> Response:
    """Stub — full implementation lands in PR #4 (Image domain)."""
    await _require_resource(session, project_id, resource_id)
    raise BadRequest(
        "image ingestion lands in PR #4", code="not_implemented_yet"
    )
