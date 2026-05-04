"""Annotation routes — /api/projects/{pid}/labelsets/{lsid}/annotations."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain.annotations import (
    AnnotationsPatchRequest,
    AnnotationsPatchResponse,
    LabelSetAnnotation,
    LabelSetAnnotations,
    NormRect,
)
from ..domain.projects import OkResponse
from ..errors import NotFound
from ..services.annotations_patch import apply_patch
from ..storage.repo.annotations import (
    Annotation,
    AnnotationRepo,
    data_load,
)
from ..storage.repo.labelsets import LabelSetRepo
from ..storage.repo.projects import ProjectRepo
from ._deps import current_user_id, get_session

router = APIRouter(
    prefix="/api/projects/{project_id}/labelsets/{labelset_id}/annotations",
    tags=["annotations"],
)


def _to_dto(row: Annotation) -> LabelSetAnnotation:
    d = data_load(row.data_json)
    rect = None
    polygon = None
    if row.kind == "rect":
        rect = NormRect(
            x=float(d.get("x", 0.0)),
            y=float(d.get("y", 0.0)),
            w=float(d.get("w", 0.0)),
            h=float(d.get("h", 0.0)),
        )
    elif row.kind == "polygon":
        polygon = d.get("polygon") or []
    return LabelSetAnnotation(
        id=row.id,
        image_id=row.image_id,
        class_id=row.class_id,
        kind=row.kind,  # type: ignore[arg-type]
        rect=rect,
        polygon=polygon,
    )


async def _require_labelset(
    session: AsyncSession, project_id: str, labelset_id: str
) -> str:
    if await ProjectRepo(session).get(project_id) is None:
        raise NotFound("project_not_found", f"project {project_id} not found")
    row = await LabelSetRepo(session).get(project_id, labelset_id)
    if row is None:
        raise NotFound(
            "labelset_not_found", f"labelset {labelset_id} not found"
        )
    return row.id


@router.get("", response_model=LabelSetAnnotations)
async def get_annotations(
    project_id: str,
    labelset_id: str,
    session: AsyncSession = Depends(get_session),
    _user: str = Depends(current_user_id),
) -> LabelSetAnnotations:
    lsid = await _require_labelset(session, project_id, labelset_id)
    rows = await AnnotationRepo(session).list_for_labelset(lsid)
    return LabelSetAnnotations(
        label_set_id=lsid,
        annotations=[_to_dto(r) for r in rows],
    )


@router.put("", response_model=OkResponse)
async def put_annotations(
    project_id: str,
    labelset_id: str,
    body: LabelSetAnnotations,
    session: AsyncSession = Depends(get_session),
    _user: str = Depends(current_user_id),
) -> OkResponse:
    """Full replace — wipes the labelset's annotations and inserts the
    payload. Used by import flows.
    """
    lsid = await _require_labelset(session, project_id, labelset_id)
    from ..services.annotations_patch import _serialize

    items = [_serialize(it) for it in body.annotations]
    await AnnotationRepo(session).replace_all(lsid, items)
    return OkResponse(ok=True)


@router.patch("", response_model=AnnotationsPatchResponse)
async def patch_annotations(
    project_id: str,
    labelset_id: str,
    body: AnnotationsPatchRequest,
    session: AsyncSession = Depends(get_session),
    _user: str = Depends(current_user_id),
) -> AnnotationsPatchResponse:
    """Apply replaceImageIds → deleteIds → upsert in a single transaction
    and return the full post-patch list.
    """
    lsid = await _require_labelset(session, project_id, labelset_id)
    rows = await apply_patch(
        session,
        labelset_id=lsid,
        upsert=body.upsert,
        delete_ids=body.delete_ids,
        replace_image_ids=body.replace_image_ids,
    )
    return AnnotationsPatchResponse(annotations=[_to_dto(r) for r in rows])
