from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from . import metrics


class JobLimiter:
    """Semaphore wrapper that also exposes queue/in-flight counts as metrics."""

    def __init__(self, max_concurrent: int) -> None:
        self._sem = asyncio.Semaphore(max_concurrent)
        self._waiting = 0
        self._active = 0
        self._lock = asyncio.Lock()

    @property
    def active(self) -> int:
        return self._active

    @property
    def waiting(self) -> int:
        return self._waiting

    @asynccontextmanager
    async def slot(self):
        async with self._lock:
            self._waiting += 1
            metrics.QUEUE_LENGTH.set(self._waiting)
        try:
            await self._sem.acquire()
        finally:
            async with self._lock:
                self._waiting -= 1
                metrics.QUEUE_LENGTH.set(self._waiting)
        async with self._lock:
            self._active += 1
            metrics.CONCURRENT_JOBS.set(self._active)
        try:
            yield
        finally:
            async with self._lock:
                self._active -= 1
                metrics.CONCURRENT_JOBS.set(self._active)
            self._sem.release()
