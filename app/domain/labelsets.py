"""LabelSet domain models — /api/projects/{pid}/labelsets.

Type is fixed at creation. One image may belong to multiple LabelSets;
class identity does not cross set boundaries.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import ApiModel

LabelSetType = Literal["polygon", "bbox", "classify"]


class LabelClass(ApiModel):
    id: str
    name: str
    color: str | None = None


class LabelSetClassStat(ApiModel):
    class_id: str
    image_count: int = 0
    annotation_count: int = 0


class LabelSetCreate(ApiModel):
    name: str = Field(..., min_length=1, max_length=200)
    type: LabelSetType
    description: str | None = None
    image_ids: list[str] = Field(default_factory=list)


class LabelSetUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    classes: list[LabelClass] | None = None
    image_ids: list[str] | None = None
    excluded_image_ids: list[str] | None = None


class LabelSet(ApiModel):
    id: str
    project_id: str
    name: str
    type: LabelSetType
    description: str | None = None
    classes: list[LabelClass] = Field(default_factory=list)
    image_ids: list[str] = Field(default_factory=list)
    excluded_image_ids: list[str] = Field(default_factory=list)
    created_at: int


class LabelSetListItem(LabelSet):
    """Lightweight list view: counts and per-class stats only — no
    imageShapes / imageLabels.
    """

    image_count: int = 0
    annotation_count: int = 0
    labeled_image_count: int = 0
    excluded_image_count: int = 0
    class_stats: list[LabelSetClassStat] = Field(default_factory=list)


class LabelSetSummary(LabelSetListItem):
    """Heavy summary; includes per-image labels and shapes for renderers
    that actually need them (workspace minimap, export validator).

    PR #5 ships these as empty dicts — PR #6 fills them once the
    annotations table exists.
    """

    image_labels: dict[str, list[str]] = Field(default_factory=dict)
    image_shapes: dict[str, list[dict]] = Field(default_factory=dict)


class LabelSetListResponse(ApiModel):
    labelsets: list[LabelSetListItem]


class LabelSetResponse(ApiModel):
    labelset: LabelSet


class LabelSetSummaryResponse(ApiModel):
    summary: LabelSetSummary
