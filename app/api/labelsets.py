"""LabelSet routes — /api/projects/{pid}/labelsets.

list (lightweight) ↔ summary (heavy) split per the contract: list returns
counts + classStats, /summary adds imageLabels + imageShapes.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain.labelsets import (
    LabelClass,
    LabelSet as LabelSetDTO,
    LabelSetCreate,
    LabelSetListItem,
    LabelSetListResponse,
    LabelSetResponse,
    LabelSetSummary,
    LabelSetSummaryResponse,
    LabelSetUpdate,
)
from ..domain.projects import OkResponse
from ..errors import NotFound
from ..services.labelset_stats import (
    compute_image_labels_and_shapes,
    compute_stats,
)
from ..storage.repo.labelsets import (
    LabelSet as LabelSetRow,
    LabelSetRepo,
    _list_load,
)
from ..storage.repo.projects import ProjectRepo
from ._deps import current_user_id, get_session

router = APIRouter(prefix="/api/projects/{project_id}/labelsets", tags=["labelsets"])


def _parse_classes(raw: str) -> list[LabelClass]:
    out: list[LabelClass] = []
    for entry in _list_load(raw):
        if isinstance(entry, dict):
            try:
                out.append(LabelClass.model_validate(entry))
            except Exception:
                continue
    return out


def _parse_id_list(raw: str) -> list[str]:
    return [str(x) for x in _list_load(raw)]


def _to_dto(row: LabelSetRow) -> LabelSetDTO:
    return LabelSetDTO(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        type=row.type,  # type: ignore[arg-type]
        description=row.description,
        classes=_parse_classes(row.classes_json),
        image_ids=_parse_id_list(row.image_ids_json),
        excluded_image_ids=_parse_id_list(row.excluded_image_ids_json),
        created_at=row.created_at,
    )


async def _to_list_item(
    session: AsyncSession, row: LabelSetRow
) -> LabelSetListItem:
    base = _to_dto(row)
    classes_dump = [c.model_dump(by_alias=False) for c in base.classes]
    stats = await compute_stats(
        session,
        image_ids=base.image_ids,
        excluded_image_ids=base.excluded_image_ids,
        classes=classes_dump,
        labelset_id=row.id,
    )
    return LabelSetListItem(
        **base.model_dump(by_alias=False),
        image_count=stats.image_count,
        annotation_count=stats.annotation_count,
        labeled_image_count=stats.labeled_image_count,
        excluded_image_count=stats.excluded_image_count,
        class_stats=stats.class_stats,  # type: ignore[arg-type]
    )


async def _require_project(session: AsyncSession, project_id: str) -> None:
    if await ProjectRepo(session).get(project_id) is None:
        raise NotFound("project_not_found", f"project {project_id} not found")


async def _require_labelset(
    session: AsyncSession, project_id: str, labelset_id: str
) -> LabelSetRow:
    await _require_project(session, project_id)
    row = await LabelSetRepo(session).get(project_id, labelset_id)
    if row is None:
        raise NotFound(
            "labelset_not_found", f"labelset {labelset_id} not found"
        )
    return row


# --- list / get / summary -------------------------------------------------


@router.get("", response_model=LabelSetListResponse)
async def list_labelsets(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    _user: str = Depends(current_user_id),
) -> LabelSetListResponse:
    await _require_project(session, project_id)
    rows = await LabelSetRepo(session).list_for_project(project_id)
    items = [await _to_list_item(session, r) for r in rows]
    return LabelSetListResponse(labelsets=items)


@router.get("/{labelset_id}", response_model=LabelSetResponse)
async def get_labelset(
    project_id: str,
    labelset_id: str,
    session: AsyncSession = Depends(get_session),
    _user: str = Depends(current_user_id),
) -> LabelSetResponse:
    row = await _require_labelset(session, project_id, labelset_id)
    return LabelSetResponse(labelset=_to_dto(row))


@router.get("/{labelset_id}/summary")
async def get_summary(
    project_id: str,
    labelset_id: str,
    session: AsyncSession = Depends(get_session),
    _user: str = Depends(current_user_id),
) -> Response:
    row = await _require_labelset(session, project_id, labelset_id)
    list_item = await _to_list_item(session, row)
    image_labels, image_shapes = await compute_image_labels_and_shapes(
        session, labelset_id=row.id
    )
    summary = LabelSetSummary(
        **list_item.model_dump(by_alias=False),
        image_labels=image_labels,
        image_shapes=image_shapes,
    )
    payload = LabelSetSummaryResponse(summary=summary).model_dump(by_alias=True)
    # The frontend revalidates after every annotation save; cheapest is to
    # opt out of caching entirely. (Open question #1 confirmed.)
    return Response(
        content=__import__("json").dumps(payload),
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


# --- create / patch / delete ----------------------------------------------


@router.post(
    "",
    response_model=LabelSetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_labelset(
    project_id: str,
    body: LabelSetCreate,
    session: AsyncSession = Depends(get_session),
    _user: str = Depends(current_user_id),
) -> LabelSetResponse:
    await _require_project(session, project_id)
    row = await LabelSetRepo(session).create(
        project_id=project_id,
        name=body.name.strip(),
        type_=body.type,
        description=body.description,
        image_ids=body.image_ids,
    )
    return LabelSetResponse(labelset=_to_dto(row))


@router.patch("/{labelset_id}", response_model=LabelSetResponse)
async def patch_labelset(
    project_id: str,
    labelset_id: str,
    body: LabelSetUpdate,
    session: AsyncSession = Depends(get_session),
    _user: str = Depends(current_user_id),
) -> LabelSetResponse:
    row = await _require_labelset(session, project_id, labelset_id)
    classes_dump = (
        [c.model_dump(by_alias=False) for c in body.classes]
        if body.classes is not None
        else None
    )
    name = body.name.strip() if body.name is not None else None
    row = await LabelSetRepo(session).update(
        row,
        name=name,
        description=body.description,
        classes=classes_dump,
        image_ids=body.image_ids,
        excluded_image_ids=body.excluded_image_ids,
    )
    return LabelSetResponse(labelset=_to_dto(row))


@router.delete("/{labelset_id}", response_model=OkResponse)
async def delete_labelset(
    project_id: str,
    labelset_id: str,
    session: AsyncSession = Depends(get_session),
    _user: str = Depends(current_user_id),
) -> OkResponse:
    deleted = await LabelSetRepo(session).delete(project_id, labelset_id)
    if not deleted:
        await _require_project(session, project_id)
        raise NotFound(
            "labelset_not_found", f"labelset {labelset_id} not found"
        )
    return OkResponse(ok=True)
