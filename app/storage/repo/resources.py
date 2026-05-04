"""Resource ORM + repository."""
from __future__ import annotations

import json

from sqlalchemy import (
    BigInteger,
    Float,
    ForeignKey,
    Integer,
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


class Resource(Base):
    __tablename__ = "resources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Stored as TEXT + CHECK so the same DDL works on sqlite and postgres.
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # tags: JSON-serialized list[str]. Generic JSON maps to JSONB on
    # postgres and TEXT on sqlite — fine for the access pattern (list-only).
    tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    ingest_via: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_blob_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    preview_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


def _tags_load(raw: str) -> list[str]:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        if isinstance(v, list):
            return [str(x) for x in v]
    except json.JSONDecodeError:
        pass
    return []


def _tags_dump(tags: list[str] | None) -> str:
    return json.dumps(list(tags or []))


class ResourceRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def create(
        self,
        *,
        project_id: str,
        type_: str,
        name: str,
        tags: list[str],
        width: int | None,
        height: int | None,
        duration: float | None,
        ingest_via: str | None,
    ) -> Resource:
        row = Resource(
            id=new_id(),
            project_id=project_id,
            type=type_,
            name=name,
            tags_json=_tags_dump(tags),
            width=width,
            height=height,
            duration=duration,
            ingest_via=ingest_via,
            source_blob_key=None,
            source_size=None,
            source_content_type=None,
            preview_count=0,
            created_at=now_ms(),
        )
        self.s.add(row)
        await self.s.flush()
        return row

    async def get(self, project_id: str, resource_id: str) -> Resource | None:
        row = await self.s.get(Resource, resource_id)
        if row is None or row.project_id != project_id:
            return None
        return row

    async def list_for_project(self, project_id: str) -> list[Resource]:
        result = await self.s.execute(
            select(Resource)
            .where(Resource.project_id == project_id)
            .order_by(Resource.created_at.desc())
        )
        return list(result.scalars().all())

    async def update(
        self,
        row: Resource,
        *,
        name: str | None,
        tags: list[str] | None,
    ) -> Resource:
        if name is not None:
            row.name = name
        if tags is not None:
            row.tags_json = _tags_dump(tags)
        await self.s.flush()
        return row

    async def set_source(
        self,
        row: Resource,
        *,
        key: str,
        size: int,
        content_type: str,
    ) -> Resource:
        row.source_blob_key = key
        row.source_size = size
        row.source_content_type = content_type
        await self.s.flush()
        return row

    async def set_preview_count(self, row: Resource, count: int) -> Resource:
        row.preview_count = count
        await self.s.flush()
        return row

    async def delete(self, project_id: str, resource_id: str) -> Resource | None:
        row = await self.get(project_id, resource_id)
        if row is None:
            return None
        await self.s.delete(row)
        await self.s.flush()
        return row

    async def bulk_delete(
        self, project_id: str, resource_ids: list[str]
    ) -> list[Resource]:
        if not resource_ids:
            return []
        result = await self.s.execute(
            select(Resource).where(
                Resource.project_id == project_id,
                Resource.id.in_(resource_ids),
            )
        )
        rows = list(result.scalars().all())
        for r in rows:
            await self.s.delete(r)
        await self.s.flush()
        return rows

    async def count_for_project(self, project_id: str) -> int:
        result = await self.s.execute(
            select(func.count(Resource.id)).where(Resource.project_id == project_id)
        )
        return int(result.scalar() or 0)
