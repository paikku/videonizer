"""Annotation ORM + repository.

Schema is portable: `data` is TEXT-stored JSON. The PATCH endpoint runs
all three operations in a single transaction (replaceImageIds → deleteIds
→ upsert) and the route returns the full post-patch list.
"""
from __future__ import annotations

import json
from typing import Iterable

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

from ...domain.common import now_ms
from ..db import Base


class Annotation(Base):
    __tablename__ = "annotations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    labelset_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("labelsets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    image_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("images.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    class_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    data_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


def data_load(raw: str) -> dict:
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except json.JSONDecodeError:
        return {}


def data_dump(d: dict) -> str:
    return json.dumps(d or {}, separators=(",", ":"))


class AnnotationRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def list_for_labelset(self, labelset_id: str) -> list[Annotation]:
        result = await self.s.execute(
            select(Annotation)
            .where(Annotation.labelset_id == labelset_id)
            .order_by(Annotation.created_at.asc())
        )
        return list(result.scalars().all())

    async def replace_for_image_ids(
        self, labelset_id: str, image_ids: Iterable[str]
    ) -> int:
        ids = list(image_ids)
        if not ids:
            return 0
        result = await self.s.execute(
            delete(Annotation).where(
                Annotation.labelset_id == labelset_id,
                Annotation.image_id.in_(ids),
            )
        )
        return int(result.rowcount or 0)

    async def delete_by_ids(
        self, labelset_id: str, ann_ids: Iterable[str]
    ) -> int:
        ids = list(ann_ids)
        if not ids:
            return 0
        result = await self.s.execute(
            delete(Annotation).where(
                Annotation.labelset_id == labelset_id,
                Annotation.id.in_(ids),
            )
        )
        return int(result.rowcount or 0)

    async def upsert(
        self,
        labelset_id: str,
        items: list[dict],
    ) -> int:
        """`items` are pre-serialized dicts:
        {id, image_id, class_id, kind, data}.

        Implemented as get-or-update / insert in a loop so the same code
        works on sqlite and postgres without dialect-specific INSERT...ON
        CONFLICT. Volume per PATCH is small (single-image edits).
        """
        if not items:
            return 0
        # Pre-fetch existing ids in one query so we don't N+1 on get().
        existing_ids = {it["id"] for it in items}
        existing = {}
        if existing_ids:
            result = await self.s.execute(
                select(Annotation).where(
                    Annotation.labelset_id == labelset_id,
                    Annotation.id.in_(existing_ids),
                )
            )
            existing = {row.id: row for row in result.scalars().all()}

        for it in items:
            row = existing.get(it["id"])
            if row is None:
                row = Annotation(
                    id=it["id"],
                    labelset_id=labelset_id,
                    image_id=it["image_id"],
                    class_id=it["class_id"],
                    kind=it["kind"],
                    data_json=data_dump(it["data"]),
                    created_at=now_ms(),
                )
                self.s.add(row)
            else:
                row.image_id = it["image_id"]
                row.class_id = it["class_id"]
                row.kind = it["kind"]
                row.data_json = data_dump(it["data"])
        await self.s.flush()
        return len(items)

    async def replace_all(
        self, labelset_id: str, items: list[dict]
    ) -> int:
        await self.s.execute(
            delete(Annotation).where(Annotation.labelset_id == labelset_id)
        )
        return await self.upsert(labelset_id, items)

    async def count_for_labelset(self, labelset_id: str) -> int:
        result = await self.s.execute(
            select(func.count(Annotation.id)).where(
                Annotation.labelset_id == labelset_id
            )
        )
        return int(result.scalar() or 0)

    async def distinct_labeled_image_ids(self, labelset_id: str) -> set[str]:
        result = await self.s.execute(
            select(Annotation.image_id).where(
                Annotation.labelset_id == labelset_id
            )
        )
        return {row[0] for row in result.all()}

    async def class_stats(
        self, labelset_id: str
    ) -> list[tuple[str, int, int]]:
        """Returns rows of (class_id, image_count, annotation_count)."""
        # annotation count: simple GROUP BY
        ac_rows = await self.s.execute(
            select(Annotation.class_id, func.count(Annotation.id))
            .where(Annotation.labelset_id == labelset_id)
            .group_by(Annotation.class_id)
        )
        ac = {r[0]: int(r[1]) for r in ac_rows.all()}
        # image count: distinct image per class
        ic_rows = await self.s.execute(
            select(
                Annotation.class_id,
                func.count(func.distinct(Annotation.image_id)),
            )
            .where(Annotation.labelset_id == labelset_id)
            .group_by(Annotation.class_id)
        )
        ic = {r[0]: int(r[1]) for r in ic_rows.all()}
        all_classes = sorted(set(ac.keys()) | set(ic.keys()))
        return [(c, ic.get(c, 0), ac.get(c, 0)) for c in all_classes]
