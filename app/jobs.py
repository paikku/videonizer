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

    async def acquire(self, *, timeout: float | None = None) -> None:
        """Acquire a worker slot, blocking until one is free.

        When `timeout` is provided and elapses before a slot is available,
        raises :class:`asyncio.TimeoutError` and leaves the limiter state
        unchanged. The matching :meth:`release` must be called exactly
        once after a successful acquire.
        """
        async with self._lock:
            self._waiting += 1
            metrics.QUEUE_LENGTH.set(self._waiting)
        try:
            if timeout is None:
                await self._sem.acquire()
            else:
                await asyncio.wait_for(self._sem.acquire(), timeout=timeout)
        finally:
            async with self._lock:
                self._waiting -= 1
                metrics.QUEUE_LENGTH.set(self._waiting)
        async with self._lock:
            self._active += 1
            metrics.CONCURRENT_JOBS.set(self._active)

    async def release(self) -> None:
        """Release a slot previously taken by :meth:`acquire`."""
        async with self._lock:
            self._active -= 1
            metrics.CONCURRENT_JOBS.set(self._active)
        self._sem.release()

    @asynccontextmanager
    async def slot(self):
        await self.acquire()
        try:
            yield
        finally:
            await self.release()
