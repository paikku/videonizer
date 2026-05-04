"""LabelSet ORM + repository."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    String,
    Text,
    delete,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from ...domain.common import new_id, now_ms
from ..db import Base


class LabelSet(Base):
    __tablename__ = "labelsets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    classes_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    image_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    excluded_image_ids_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]"
    )
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


def _list_load(raw: str) -> list[Any]:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except json.JSONDecodeError:
        return []


def _list_dump(v: list[Any] | None) -> str:
    return json.dumps(list(v or []))


class LabelSetRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def create(
        self,
        *,
        project_id: str,
        name: str,
        type_: str,
        description: str | None,
        image_ids: list[str],
    ) -> LabelSet:
        row = LabelSet(
            id=new_id(),
            project_id=project_id,
            name=name,
            type=type_,
            description=description,
            classes_json="[]",
            image_ids_json=_list_dump(image_ids),
            excluded_image_ids_json="[]",
            created_at=now_ms(),
        )
        self.s.add(row)
        await self.s.flush()
        return row

    async def get(self, project_id: str, labelset_id: str) -> LabelSet | None:
        row = await self.s.get(LabelSet, labelset_id)
        if row is None or row.project_id != project_id:
            return None
        return row

    async def list_for_project(self, project_id: str) -> list[LabelSet]:
        result = await self.s.execute(
            select(LabelSet)
            .where(LabelSet.project_id == project_id)
            .order_by(LabelSet.created_at.desc())
        )
        return list(result.scalars().all())

    async def update(
        self,
        row: LabelSet,
        *,
        name: str | None = None,
        description: str | None = None,
        classes: list[dict] | None = None,
        image_ids: list[str] | None = None,
        excluded_image_ids: list[str] | None = None,
    ) -> LabelSet:
        if name is not None:
            row.name = name
        if description is not None:
            row.description = description
        if classes is not None:
            row.classes_json = _list_dump(classes)
        if image_ids is not None:
            row.image_ids_json = _list_dump(image_ids)
        if excluded_image_ids is not None:
            row.excluded_image_ids_json = _list_dump(excluded_image_ids)
        await self.s.flush()
        return row

    async def delete(self, project_id: str, labelset_id: str) -> bool:
        row = await self.get(project_id, labelset_id)
        if row is None:
            return False
        await self.s.delete(row)
        await self.s.flush()
        return True

    async def count_for_project(self, project_id: str) -> int:
        result = await self.s.execute(
            select(func.count(LabelSet.id)).where(LabelSet.project_id == project_id)
        )
        return int(result.scalar() or 0)
