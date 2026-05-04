"""Annotation domain models — /api/projects/{pid}/labelsets/{lsid}/annotations.

Tagged union: rect / polygon / classify. Coordinates normalized to [0, 1]
on both axes. Server stores `data` as JSONB (postgres) / TEXT (sqlite).
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import ApiModel


AnnotationKind = Literal["rect", "polygon", "classify"]


class NormRect(ApiModel):
    x: float = Field(..., ge=0.0, le=1.0)
    y: float = Field(..., ge=0.0, le=1.0)
    w: float = Field(..., ge=0.0, le=1.0)
    h: float = Field(..., ge=0.0, le=1.0)


class LabelSetAnnotation(ApiModel):
    id: str
    image_id: str
    class_id: str
    kind: AnnotationKind
    rect: NormRect | None = None
    polygon: list[list[list[float]]] | None = None  # rings of [x,y]


class LabelSetAnnotations(ApiModel):
    label_set_id: str
    annotations: list[LabelSetAnnotation]


class AnnotationsPatchRequest(ApiModel):
    upsert: list[LabelSetAnnotation] | None = None
    delete_ids: list[str] | None = None
    replace_image_ids: list[str] | None = None


class AnnotationsPatchResponse(ApiModel):
    annotations: list[LabelSetAnnotation]


# --- Export ----------------------------------------------------------------


SplitName = Literal["train", "val", "test"]


class SplitConfig(ApiModel):
    train: float = Field(default=0.8, ge=0.0, le=1.0)
    val: float = Field(default=0.1, ge=0.0, le=1.0)
    test: float = Field(default=0.1, ge=0.0, le=1.0)


class ValidationReport(ApiModel):
    total_images: int
    labeled_images: int
    unlabeled_images: int
    split_counts: dict[str, int]
    warnings: list[str]


class ExportPreviewItem(ApiModel):
    image_id: str
    file_name: str
    split: SplitName | None = None
    excluded: bool
    labeled: bool
    tags: list[str]


class ValidateRequest(ApiModel):
    split: SplitConfig | None = None


class ValidateResponse(ApiModel):
    report: ValidationReport
    items: list[ExportPreviewItem]


class DatasetRequest(ApiModel):
    split: SplitConfig | None = None
    include_images: bool = True
    remap_class_ids: bool = False
