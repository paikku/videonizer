"""Annotations PATCH executor.

Runs replaceImageIds → deleteIds → upsert in a single transaction (the
caller's session, which auto-commits on success). Returns the full
post-patch annotation list so the client can resync without an extra GET.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ..domain.annotations import LabelSetAnnotation
from ..storage.repo.annotations import Annotation, AnnotationRepo


def _serialize(item: LabelSetAnnotation) -> dict:
    """Pull `data` for the annotation kind. Coordinates are normalized
    [0, 1]. The DB column is opaque JSON; the kind discriminator picks
    the schema.
    """
    payload: dict = {}
    if item.kind == "rect" and item.rect is not None:
        payload = {
            "x": item.rect.x,
            "y": item.rect.y,
            "w": item.rect.w,
            "h": item.rect.h,
        }
    elif item.kind == "polygon" and item.polygon is not None:
        payload = {"polygon": item.polygon}
    # classify carries no geometry; data stays {}.
    return {
        "id": item.id,
        "image_id": item.image_id,
        "class_id": item.class_id,
        "kind": item.kind,
        "data": payload,
    }


async def apply_patch(
    session: AsyncSession,
    *,
    labelset_id: str,
    upsert: list[LabelSetAnnotation] | None,
    delete_ids: list[str] | None,
    replace_image_ids: list[str] | None,
) -> list[Annotation]:
    repo = AnnotationRepo(session)

    # 1. replaceImageIds — wipe annotations on those images first
    if replace_image_ids:
        await repo.replace_for_image_ids(labelset_id, replace_image_ids)

    # 2. deleteIds — remove specific annotations
    if delete_ids:
        await repo.delete_by_ids(labelset_id, delete_ids)

    # 3. upsert — apply the new state
    if upsert:
        items = [_serialize(it) for it in upsert]
        await repo.upsert(labelset_id, items)

    return await repo.list_for_labelset(labelset_id)
