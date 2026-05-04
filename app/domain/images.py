"""Image domain models — /api/projects/{pid}/images.

`source` is derived from the parent resource type (video → video_frame,
image_batch → uploaded). The frontend treats them differently for tag
edits but the storage shape is identical.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import ApiModel

ImageSource = Literal["uploaded", "video_frame"]
TagMode = Literal["add", "replace", "remove"]


class Image(ApiModel):
    id: str
    project_id: str
    resource_id: str
    source: ImageSource
    file_name: str
    width: int
    height: int
    timestamp: float | None = None
    frame_index: int | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: int


class ImageUpdate(ApiModel):
    tags: list[str] | None = None


class ImageListResponse(ApiModel):
    images: list[Image]


class ImageResponse(ApiModel):
    image: Image


class BulkDeleteImagesRequest(ApiModel):
    image_ids: list[str]


class BulkDeleteImagesResponse(ApiModel):
    deleted: int


class BulkTagsRequest(ApiModel):
    image_ids: list[str]
    tags: list[str]
    mode: TagMode


class BulkTagsResponse(ApiModel):
    updated: int


class ImageBatchResponse(ApiModel):
    images: list[Image]


class ImageMetaItem(ApiModel):
    """One entry in the `meta` JSON array sent alongside `files[]`."""

    file_name: str
    width: int
    height: int
    timestamp: float | None = None
    frame_index: int | None = None
    id: str | None = None
