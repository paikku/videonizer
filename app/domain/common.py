"""Pydantic helpers shared across domain modules."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    """Base model for API request/response bodies. JSON via camelCase to
    match the frontend ``types.ts`` contract.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,
        alias_generator=lambda s: _to_camel(s),
    )


def _to_camel(snake: str) -> str:
    head, *rest = snake.split("_")
    return head + "".join(w.title() for w in rest)


def new_id() -> str:
    """Server-generated UUID. UUID4 (random) — uuid7 isn't in stdlib yet
    and the time-sortable property isn't needed for any current query.
    """
    return str(uuid.uuid4())


def now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)
