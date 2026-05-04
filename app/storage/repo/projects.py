"""Project ORM + repository.

ORM stays plain — no ENUM/JSONB/UUID column types so SQLite tests
exercise the same DDL as Postgres production.
"""
from __future__ import annotations

from sqlalchemy import String, BigInteger, select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from ...domain.common import new_id, now_ms
from ..db import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ProjectRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def create(self, *, name: str) -> Project:
        row = Project(id=new_id(), name=name, created_at=now_ms())
        self.s.add(row)
        await self.s.flush()
        return row

    async def get(self, project_id: str) -> Project | None:
        return await self.s.get(Project, project_id)

    async def list_all(self) -> list[Project]:
        result = await self.s.execute(
            select(Project).order_by(Project.created_at.desc())
        )
        return list(result.scalars().all())

    async def delete(self, project_id: str) -> bool:
        result = await self.s.execute(
            delete(Project).where(Project.id == project_id)
        )
        return (result.rowcount or 0) > 0
