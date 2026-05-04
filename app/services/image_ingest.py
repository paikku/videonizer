"""Image ingestion: bytes + meta → blob put + thumbnail + DB row.

Used by ``POST /api/projects/{pid}/resources/{rid}/images``.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import UploadFile

from ..domain.common import new_id
from ..domain.images import ImageMetaItem
from ..errors import BadRequest, ValidationError
from ..storage.blobs import BlobStore
from ..storage.repo.images import Image, ImageRepo
from ..storage.repo.resources import Resource
from .blob_keys import image_bytes_key, image_thumb_key, safe_extension
from .thumbnails import make_thumbnail
from .uploads import buffer_to_memory


@dataclass
class IngestedImage:
    image: Image


def _source_for_resource(resource: Resource) -> str:
    return "video_frame" if resource.type == "video" else "uploaded"


async def ingest_one(
    *,
    project_id: str,
    resource: Resource,
    upload: UploadFile,
    meta: ImageMetaItem,
    store: BlobStore,
    repo: ImageRepo,
    upload_limit: int,
) -> Image:
    if meta.width <= 0 or meta.height <= 0:
        raise ValidationError("width/height must be positive")

    # Idempotence: if the client passed an id that already exists for this
    # project, just return the existing row instead of re-uploading.
    if meta.id is not None:
        existing = await repo.get(project_id, meta.id)
        if existing is not None:
            if existing.resource_id != resource.id:
                raise BadRequest(
                    "image id already used in another resource",
                    code="image_id_conflict",
                )
            return existing

    image_id = meta.id or new_id()
    raw = await buffer_to_memory(upload, limit=upload_limit)
    if not raw:
        raise BadRequest("empty image upload", code="empty_file")

    ext = safe_extension(upload.filename) or safe_extension(meta.file_name) or "bin"
    bytes_key = image_bytes_key(project_id, image_id, ext)
    thumb_key = image_thumb_key(project_id, image_id)

    # Generate thumbnail BEFORE writing anything so PIL failure doesn't
    # leave orphan blobs.
    thumb_bytes = await make_thumbnail(raw)

    content_type = upload.content_type or "application/octet-stream"
    bytes_meta = await store.put_bytes(bytes_key, raw, content_type)
    await store.put_bytes(thumb_key, thumb_bytes, "image/jpeg")

    return await repo.create(
        project_id=project_id,
        resource_id=resource.id,
        source=_source_for_resource(resource),
        file_name=meta.file_name,
        width=meta.width,
        height=meta.height,
        timestamp=meta.timestamp,
        frame_index=meta.frame_index,
        tags=[],
        bytes_blob_key=bytes_key,
        bytes_size=bytes_meta.size,
        bytes_content_type=bytes_meta.content_type or content_type,
        thumb_blob_key=thumb_key,
        client_id=image_id,
    )
