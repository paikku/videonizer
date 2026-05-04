"""Export routes — JSON download, validate (dry-run), dataset ZIP stream."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain.annotations import (
    DatasetRequest,
    SplitConfig,
    ValidateRequest,
    ValidateResponse,
)
from ..errors import NotFound
from ..services.export_validate import validate as run_validate
from ..services.export_zip import build_zip_stream
from ..storage.blobs import BlobStore
from ..storage.repo.labelsets import LabelSetRepo
from ..storage.repo.projects import ProjectRepo
from ._deps import current_user_id, get_session, get_store

router = APIRouter(
    prefix="/api/projects/{project_id}/labelsets/{labelset_id}/export",
    tags=["export"],
)


async def _require(session: AsyncSession, project_id: str, labelset_id: str):
    if await ProjectRepo(session).get(project_id) is None:
        raise NotFound("project_not_found", f"project {project_id} not found")
    row = await LabelSetRepo(session).get(project_id, labelset_id)
    if row is None:
        raise NotFound(
            "labelset_not_found", f"labelset {labelset_id} not found"
        )
    return row


@router.get("")
async def export_json(
    project_id: str,
    labelset_id: str,
    session: AsyncSession = Depends(get_session),
    _user: str = Depends(current_user_id),
) -> Response:
    """Annotations-only download (no images). Mirrors the dataset ZIP's
    annotations.json shape so the same parser works for both.
    """
    labelset = await _require(session, project_id, labelset_id)

    # Reuse validate to enumerate items + split assignments deterministically.
    result = await run_validate(session, labelset=labelset, split=None)
    items = [it.model_dump(by_alias=True) for it in result.items]
    from ..services.export_zip import _build_annotations_payload  # local
    from ..storage.repo.annotations import AnnotationRepo

    annotations = await AnnotationRepo(session).list_for_labelset(labelset.id)
    payload = _build_annotations_payload(
        labelset, annotations, items, remap_class_ids=False
    )
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    safe_name = labelset.name.replace('"', "").replace("/", "_") or "labelset"
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}.json"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/validate", response_model=ValidateResponse)
async def export_validate(
    project_id: str,
    labelset_id: str,
    body: ValidateRequest,
    session: AsyncSession = Depends(get_session),
    _user: str = Depends(current_user_id),
) -> ValidateResponse:
    labelset = await _require(session, project_id, labelset_id)
    result = await run_validate(
        session, labelset=labelset, split=body.split or SplitConfig()
    )
    return ValidateResponse(report=result.report, items=result.items)


@router.post("/dataset")
async def export_dataset(
    project_id: str,
    labelset_id: str,
    body: DatasetRequest,
    session: AsyncSession = Depends(get_session),
    store: BlobStore = Depends(get_store),
    _user: str = Depends(current_user_id),
) -> StreamingResponse:
    labelset = await _require(session, project_id, labelset_id)
    result = await run_validate(
        session, labelset=labelset, split=body.split or SplitConfig()
    )
    items = [it.model_dump(by_alias=True) for it in result.items]

    safe_name = labelset.name.replace('"', "").replace("/", "_") or "labelset"
    body_iter = build_zip_stream(
        session=session,
        store=store,
        labelset=labelset,
        items=items,
        include_images=body.include_images,
        remap_class_ids=body.remap_class_ids,
    )
    return StreamingResponse(
        body_iter,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}.zip"',
            "Cache-Control": "no-store",
        },
    )
