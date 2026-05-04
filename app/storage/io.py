"""Atomic JSON / bytes IO + per-path async locks.

Writes stage to a sibling tmp file, then ``os.replace`` onto the target.
A reader that opens the file mid-write either sees the previous contents
or the new contents — never a partial write.

Locks are keyed on the resolved absolute path so two callers reaching the
same file by different relative paths still serialize.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")

_locks: dict[str, asyncio.Lock] = {}
_locks_mu = asyncio.Lock()


def _resolve(path: os.PathLike | str) -> str:
    return str(Path(path).resolve())


async def _get_lock(path: os.PathLike | str) -> asyncio.Lock:
    key = _resolve(path)
    async with _locks_mu:
        lock = _locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _locks[key] = lock
        return lock


async def with_file_lock(
    path: os.PathLike | str,
    fn: Callable[[], Awaitable[T] | T],
) -> T:
    """Run ``fn`` while holding the per-path async lock.

    ``fn`` may be sync or async. Returns whatever the inner call returns.
    """
    lock = await _get_lock(path)
    async with lock:
        result = fn()
        if asyncio.iscoroutine(result):
            result = await result
        return result  # type: ignore[return-value]


def clear_locks() -> None:
    """Test-only helper. Production code never calls this.

    asyncio.Locks are bound to whichever event loop first uses them, so a
    test that spins up a new loop will trip ``RuntimeError: ... bound to a
    different event loop`` if it inherits locks from a prior test. Tests
    call this in their fixture's teardown to start each loop clean.
    """
    _locks.clear()


# --- JSON --------------------------------------------------------------


def _read_json_sync(path: Path, fallback: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return fallback


async def read_json(path: Path, fallback: T) -> T:
    return await asyncio.to_thread(_read_json_sync, path, fallback)


def _write_json_sync(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".tmp.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


async def write_json(path: Path, value: Any) -> None:
    await asyncio.to_thread(_write_json_sync, path, value)


# --- bytes -------------------------------------------------------------


async def ensure_dir(path: Path) -> None:
    await asyncio.to_thread(lambda: path.mkdir(parents=True, exist_ok=True))


def _write_bytes_sync(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".tmp.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


async def write_bytes_atomic(path: Path, data: bytes) -> None:
    await asyncio.to_thread(_write_bytes_sync, path, data)


async def read_bytes(path: Path) -> bytes | None:
    try:
        return await asyncio.to_thread(path.read_bytes)
    except FileNotFoundError:
        return None


async def unlink_quiet(path: Path) -> None:
    """``rm -f`` semantics — missing file is not an error."""

    def _unlink() -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    await asyncio.to_thread(_unlink)
