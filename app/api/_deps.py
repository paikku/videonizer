"""Common FastAPI dependencies for the stateful API."""
from __future__ import annotations

from typing import AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..storage import db
from ..storage.blobs import BlobStore, get_blob_store


async def get_session() -> AsyncIterator[AsyncSession]:
    """Auto-commit on success, rollback on exception."""
    async for s in db.session_scope():
        yield s


def get_store() -> BlobStore:
    return get_blob_store()


def current_user_id() -> str:
    """Auth-deferral hook. Every route declares this dependency so that the
    day auth lands we change one line. Until then everything is anonymous.
    """
    return "anonymous"


__all__ = ["get_session", "get_store", "current_user_id", "Depends"]
