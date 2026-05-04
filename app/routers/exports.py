"""LabelSet export routes — JSON dump, ZIP bundle, dry-run validate.

Sibling to ``labelsets.py`` so the LabelSet CRUD module stays focused.
All three endpoints share the same data-gathering shape: pull the
LabelSet + its membership images + their resources + the annotations,
then hand off to ``app.export``.
"""
from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from .. import storage
from ..errors import NotFoundError
from ..export import (
    build_bundle,
    build_labelset_export,
    split_images,
    validate_export,
)

router = APIRouter(
    prefix="/v1/projects/{project_id}/labelsets/{lsid}/export",
    tags=["labelsets"],
)


class DatasetBody(BaseModel):
    split: dict[str, Any] | None = None
    includeImages: bool | None = None
    remapClassIds: bool | None = None


class ValidateBody(BaseModel):
    split: dict[str, Any] | None = None


_BAD_FILE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_TRIM_UNDERSCORES = re.compile(r"^_+|_+$")


def _safe_file_name(name: str) -> str:
    cleaned = _BAD_FILE_CHARS.sub("_", name)
    cleaned = _TRIM_UNDERSCORES.sub("", cleaned)
    return cleaned or "labelset"


async def _gather(project_id: str, lsid: str) -> dict[str, Any] | None:
    """Pull the LabelSet + its in-set images + their resources + annotations.
    Returns None if the LabelSet doesn't exist."""
    labelset = await storage.get_labelset(project_id, lsid)
    if labelset is None:
        return None
    ann_data = await storage.get_labelset_annotations(project_id, lsid)
    all_images = await storage.list_images(project_id)
    member_set = set(labelset.get("imageIds", []))
    images = [img for img in all_images if img["id"] in member_set]
    # Preserve LabelSet image order so JSON / dataset / validate agree.
    order = {iid: i for i, iid in enumerate(labelset.get("imageIds", []))}
    images.sort(key=lambda img: order.get(img["id"], 0))
    resource_ids = []
    seen = set()
    for img in images:
        rid = img["resourceId"]
        if rid not in seen:
            seen.add(rid)
            resource_ids.append(rid)
    resources = []
    for rid in resource_ids:
        r = await storage.get_resource(project_id, rid)
        if r is not None:
            resources.append(r)
    return {
        "labelset": labelset,
        "annotations": ann_data["annotations"],
        "images": images,
        "resources": resources,
    }


@router.get("")
async def export_json(project_id: str, lsid: str) -> Response:
    g = await _gather(project_id, lsid)
    if g is None:
        raise NotFoundError(f"labelset {lsid} not found")
    payload = build_labelset_export(
        labelset=g["labelset"],
        images=g["images"],
        resources=g["resources"],
        annotations=g["annotations"],
    )
    file_name = f"{_safe_file_name(g['labelset']['name'])}.json"
    return Response(
        content=json.dumps(payload, indent=2, ensure_ascii=False),
        media_type="application/json; charset=utf-8",
        headers={
            "content-disposition": f'attachment; filename="{file_name}"',
            "cache-control": "no-store",
        },
    )


@router.post("/dataset")
async def export_dataset(
    project_id: str, lsid: str, body: DatasetBody
) -> Response:
    g = await _gather(project_id, lsid)
    if g is None:
        raise NotFoundError(f"labelset {lsid} not found")

    options = {
        "split": body.split or {"mode": "none"},
        "includeImages": bool(body.includeImages),
        "remapClassIds": bool(body.remapClassIds),
    }

    async def read_bytes(image_id: str):
        return await storage.read_image_bytes(project_id, image_id)

    result = await build_bundle(
        labelset=g["labelset"],
        images=g["images"],
        annotations=g["annotations"],
        options=options,
        read_image_bytes=read_bytes if options["includeImages"] else None,
    )
    return Response(
        content=result["zip"],
        media_type="application/zip",
        headers={
            "content-disposition": f'attachment; filename="{result["fileName"]}"',
            "cache-control": "no-store",
        },
    )


@router.post("/validate")
async def export_validate(
    project_id: str, lsid: str, body: ValidateBody
) -> JSONResponse:
    """Dry-run: returns the same counts/warnings the dataset endpoint
    would produce, plus a per-image preview the FE can render in the
    Export panel before commit.
    """
    g = await _gather(project_id, lsid)
    if g is None:
        raise NotFoundError(f"labelset {lsid} not found")

    split_config = body.split or {"mode": "none"}
    splits = split_images(
        [{"id": i["id"], "tags": i.get("tags", [])} for i in g["images"]],
        split_config,
    )
    report = validate_export(
        labelset=g["labelset"],
        images=g["images"],
        annotations=g["annotations"],
        splits=splits,
    )
    labeled_ids = {a["imageId"] for a in g["annotations"]}
    excluded_ids = set(g["labelset"].get("excludedImageIds") or [])
    items = [
        {
            "imageId": img["id"],
            "fileName": img["fileName"],
            "split": splits.get(img["id"]),
            "excluded": img["id"] in excluded_ids,
            "labeled": img["id"] in labeled_ids,
            "tags": img.get("tags", []),
        }
        for img in g["images"]
    ]
    return JSONResponse({"report": report, "items": items})
