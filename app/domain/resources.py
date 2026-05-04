"""Resource domain models — /api/projects/{id}/resources.

Resource is a single upload container: one video, or one image batch.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import ApiModel


ResourceType = Literal["video", "image_batch"]
IngestVia = Literal["client", "server"]


class Resource(ApiModel):
    id: str
    project_id: str
    type: ResourceType
    name: str
    tags: list[str] = Field(default_factory=list)
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    ingest_via: IngestVia | None = None
    has_source: bool = False
    preview_count: int = 0
    created_at: int


class ResourceSummary(Resource):
    image_count: int = 0


class ResourceUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    tags: list[str] | None = None


class ResourceListResponse(ApiModel):
    resources: list[ResourceSummary]


class ResourceResponse(ApiModel):
    resource: Resource


class BulkDeleteRequest(ApiModel):
    resource_ids: list[str]


class BulkDeleteResponse(ApiModel):
    deleted: int


class PreviewUploadResponse(ApiModel):
    preview_count: int
