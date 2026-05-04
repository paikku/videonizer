"""LabelSets API — CRUD + annotation read/replace.

Export endpoints (JSON dump, YOLO/CSV ZIP, validate dry-run) live in a
sibling module to keep this file focused on the labeling-unit data
plane.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from .. import storage
from ..errors import BadRequestError, NotFoundError

router = APIRouter(prefix="/v1/projects/{project_id}/labelsets", tags=["labelsets"])

VALID_TYPES = ("polygon", "bbox", "classify")


class CreateLabelSetBody(BaseModel):
    name: str
    type: str
    description: str | None = None
    imageIds: list[str] | None = None


class UpdateLabelSetBody(BaseModel):
    name: str | None = None
    description: str | None = None
    classes: list[dict[str, Any]] | None = None
    imageIds: list[str] | None = None
    excludedImageIds: list[str] | None = None


class AnnotationsBody(BaseModel):
    annotations: list[dict[str, Any]]


# ----- list / CRUD -----------------------------------------------------


@router.get("")
async def list_labelsets(project_id: str) -> dict:
    return {"labelsets": await storage.list_labelsets(project_id)}


@router.post("", status_code=201)
async def create_labelset(project_id: str, body: CreateLabelSetBody) -> dict:
    name = (body.name or "").strip()
    if not name:
        raise BadRequestError("name is required")
    if body.type not in VALID_TYPES:
        raise BadRequestError("type must be polygon | bbox | classify")
    ls = await storage.create_labelset(
        project_id,
        name=name,
        type=body.type,
        description=body.description,
        image_ids=body.imageIds,
    )
    return {"labelset": ls}


@router.get("/{lsid}")
async def get_labelset(project_id: str, lsid: str) -> dict:
    ls = await storage.get_labelset(project_id, lsid)
    if ls is None:
        raise NotFoundError(f"labelset {lsid} not found")
    return {"labelset": ls}


@router.patch("/{lsid}")
async def update_labelset(
    project_id: str, lsid: str, body: UpdateLabelSetBody
) -> dict:
    ls = await storage.update_labelset(
        project_id,
        lsid,
        name=body.name,
        description=body.description,
        classes=body.classes,
        image_ids=body.imageIds,
        excluded_image_ids=body.excludedImageIds,
    )
    if ls is None:
        raise NotFoundError(f"labelset {lsid} not found")
    return {"labelset": ls}


@router.delete("/{lsid}")
async def delete_labelset(project_id: str, lsid: str) -> dict:
    await storage.delete_labelset(project_id, lsid)
    return {"ok": True}


# ----- annotations -----------------------------------------------------


@router.get("/{lsid}/annotations")
async def get_annotations(project_id: str, lsid: str) -> dict:
    """Return the LabelSet's annotation list. Missing labelset still
    returns ``{ "annotations": [] }`` (matches vision's behavior — the
    file is read with a fallback).
    """
    return await storage.get_labelset_annotations(project_id, lsid)


@router.put("/{lsid}/annotations")
async def replace_annotations(
    project_id: str, lsid: str, body: AnnotationsBody
) -> dict:
    """Atomic full-replace. The body's ``annotations`` list replaces the
    server's copy; per the contract, individual diff/append is not
    supported.
    """
    await storage.save_labelset_annotations(
        project_id, lsid, {"annotations": body.annotations}
    )
    return {"ok": True}
