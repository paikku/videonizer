"""Image ORM + repository."""
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


class Image(Base):
    __tablename__ = "images"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    resource_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("resources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[float | None] = mapped_column(Float, nullable=True)
    frame_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    bytes_blob_key: Mapped[str] = mapped_column(String(512), nullable=False)
    bytes_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    bytes_content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    thumb_blob_key: Mapped[str] = mapped_column(String(512), nullable=False)
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


class ImageRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def create(
        self,
        *,
        project_id: str,
        resource_id: str,
        source: str,
        file_name: str,
        width: int,
        height: int,
        bytes_blob_key: str,
        bytes_size: int,
        bytes_content_type: str,
        thumb_blob_key: str,
        timestamp: float | None = None,
        frame_index: int | None = None,
        tags: list[str] | None = None,
        client_id: str | None = None,
    ) -> Image:
        row = Image(
            id=client_id or new_id(),
            project_id=project_id,
            resource_id=resource_id,
            source=source,
            file_name=file_name,
            width=width,
            height=height,
            timestamp=timestamp,
            frame_index=frame_index,
            tags_json=_tags_dump(tags or []),
            bytes_blob_key=bytes_blob_key,
            bytes_size=bytes_size,
            bytes_content_type=bytes_content_type,
            thumb_blob_key=thumb_blob_key,
            created_at=now_ms(),
        )
        self.s.add(row)
        await self.s.flush()
        return row

    async def get(self, project_id: str, image_id: str) -> Image | None:
        row = await self.s.get(Image, image_id)
        if row is None or row.project_id != project_id:
            return None
        return row

    async def get_by_id(self, image_id: str) -> Image | None:
        return await self.s.get(Image, image_id)

    async def list_for_project(
        self,
        project_id: str,
        *,
        resource_id: str | None = None,
        source: str | None = None,
        tag: str | None = None,
    ) -> list[Image]:
        stmt = select(Image).where(Image.project_id == project_id)
        if resource_id is not None:
            stmt = stmt.where(Image.resource_id == resource_id)
        if source is not None:
            stmt = stmt.where(Image.source == source)
        stmt = stmt.order_by(Image.created_at.desc())
        result = await self.s.execute(stmt)
        rows = list(result.scalars().all())
        # Tag filter is exact-match against JSON-serialized list. Doing it
        # in Python is fine for current scale; can add a GIN index + a
        # postgres jsonb @> expression later.
        if tag is not None:
            rows = [r for r in rows if tag in _tags_load(r.tags_json)]
        return rows

    async def list_for_resource(
        self, project_id: str, resource_id: str
    ) -> list[Image]:
        return await self.list_for_project(project_id, resource_id=resource_id)

    async def update_tags(self, row: Image, tags: list[str]) -> Image:
        row.tags_json = _tags_dump(tags)
        await self.s.flush()
        return row

    async def delete(self, project_id: str, image_id: str) -> Image | None:
        row = await self.get(project_id, image_id)
        if row is None:
            return None
        await self.s.delete(row)
        await self.s.flush()
        return row

    async def bulk_delete(
        self, project_id: str, image_ids: list[str]
    ) -> list[Image]:
        if not image_ids:
            return []
        result = await self.s.execute(
            select(Image).where(
                Image.project_id == project_id,
                Image.id.in_(image_ids),
            )
        )
        rows = list(result.scalars().all())
        for r in rows:
            await self.s.delete(r)
        await self.s.flush()
        return rows

    async def bulk_tags(
        self, project_id: str, image_ids: list[str], *, tags: list[str], mode: str
    ) -> int:
        if not image_ids:
            return 0
        result = await self.s.execute(
            select(Image).where(
                Image.project_id == project_id,
                Image.id.in_(image_ids),
            )
        )
        rows = list(result.scalars().all())
        target = list(dict.fromkeys(tags))
        for r in rows:
            current = _tags_load(r.tags_json)
            if mode == "replace":
                new = target
            elif mode == "add":
                seen = set(current)
                new = current + [t for t in target if t not in seen]
            elif mode == "remove":
                new = [t for t in current if t not in set(target)]
            else:
                raise ValueError(f"unknown tag mode: {mode}")
            r.tags_json = _tags_dump(new)
        await self.s.flush()
        return len(rows)

    async def count_for_project(self, project_id: str) -> int:
        result = await self.s.execute(
            select(func.count(Image.id)).where(Image.project_id == project_id)
        )
        return int(result.scalar() or 0)

    async def count_for_resource(self, project_id: str, resource_id: str) -> int:
        result = await self.s.execute(
            select(func.count(Image.id)).where(
                Image.project_id == project_id,
                Image.resource_id == resource_id,
            )
        )
        return int(result.scalar() or 0)
