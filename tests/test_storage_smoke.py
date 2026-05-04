"""End-to-end smoke for the PR #1 infra:

- DB engine boots, alembic head reaches 0001_baseline
- Blob store can put / head / get / range / delete / delete_prefix
- /healthz still returns ok
- /metrics still served
"""
from __future__ import annotations

import io

import pytest
from sqlalchemy import text

from app.api._range import InvalidRange, parse_range


# --- DB --------------------------------------------------------------------


async def test_db_ping(stateful_app, session_factory) -> None:
    async with session_factory() as s:
        result = await s.execute(text("SELECT 1"))
        assert result.scalar() == 1


async def test_alembic_at_head(stateful_app, session_factory) -> None:
    """alembic_version table should exist and match the head revision."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config("alembic.ini")
    head = ScriptDirectory.from_config(cfg).get_current_head()
    async with session_factory() as s:
        rev = await s.execute(text("SELECT version_num FROM alembic_version"))
        assert rev.scalar() == head


# --- Blob store ------------------------------------------------------------


async def test_blob_put_get_delete(blob_store) -> None:
    key = "p/test/r/abc/source.bin"
    payload = b"hello videonizer"
    meta = await blob_store.put_bytes(key, payload, "application/octet-stream")
    assert meta.size == len(payload)
    assert await blob_store.exists(key)

    got, head = await blob_store.get_bytes(key)
    assert got == payload
    assert head.size == len(payload)

    await blob_store.delete(key)
    assert not await blob_store.exists(key)


async def test_blob_put_stream_and_head(blob_store) -> None:
    key = "p/test/r/abc/preview.jpg"
    payload = b"\xff\xd8\xff\xe0" + b"x" * 1000
    meta = await blob_store.put_stream(key, io.BytesIO(payload), "image/jpeg")
    assert meta.size == len(payload)
    assert meta.content_type == "image/jpeg"
    head = await blob_store.head(key)
    assert head.size == len(payload)
    await blob_store.delete(key)


async def test_blob_range_read(blob_store) -> None:
    key = "p/test/r/abc/source.mp4"
    payload = bytes(range(256)) * 32  # 8 KiB
    await blob_store.put_bytes(key, payload, "video/mp4")

    body_iter, meta, total = await blob_store.get_range(key, 100, 199)
    assert total == len(payload)
    chunks = b""
    async for chunk in body_iter:
        chunks += chunk
    assert chunks == payload[100:200]
    assert meta.size == len(payload)
    await blob_store.delete(key)


async def test_blob_range_open_ended(blob_store) -> None:
    key = "p/test/r/abc/source2.mp4"
    payload = b"0123456789" * 100  # 1000 bytes
    await blob_store.put_bytes(key, payload, "video/mp4")

    body_iter, _meta, total = await blob_store.get_range(key, 990, None)
    chunks = b""
    async for chunk in body_iter:
        chunks += chunk
    assert chunks == payload[990:]
    assert total == 1000
    await blob_store.delete(key)


async def test_blob_delete_prefix(blob_store) -> None:
    base = "p/test/cascade"
    keys = [f"{base}/file{i}.bin" for i in range(5)]
    for k in keys:
        await blob_store.put_bytes(k, b"x", "application/octet-stream")
    # An unrelated key that must NOT be deleted.
    sentinel = "p/test/cascade-sibling/keep.bin"
    await blob_store.put_bytes(sentinel, b"keep", "application/octet-stream")

    deleted = await blob_store.delete_prefix(base + "/")
    assert deleted == 5
    for k in keys:
        assert not await blob_store.exists(k)
    assert await blob_store.exists(sentinel)
    await blob_store.delete(sentinel)


# --- Range parser unit tests (pure) ----------------------------------------


def test_parse_range_explicit() -> None:
    assert parse_range("bytes=0-99", 1000) == (0, 99)
    assert parse_range("bytes=500-", 1000) == (500, 999)
    assert parse_range("bytes=-100", 1000) == (900, 999)
    assert parse_range(None, 1000) is None


def test_parse_range_clamps_end() -> None:
    assert parse_range("bytes=0-9999", 1000) == (0, 999)


def test_parse_range_invalid() -> None:
    with pytest.raises(InvalidRange):
        parse_range("bytes=", 1000)
    with pytest.raises(InvalidRange):
        parse_range("garbage", 1000)
    with pytest.raises(InvalidRange):
        parse_range("bytes=2000-3000", 1000)
    with pytest.raises(InvalidRange):
        parse_range("bytes=500-100", 1000)


# --- Health / metrics regression -------------------------------------------


async def test_healthz_still_ok(stateful_app) -> None:
    r = await stateful_app.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_metrics_still_exposed(stateful_app) -> None:
    r = await stateful_app.get("/metrics")
    assert r.status_code == 200
    assert "normalize_jobs_total" in r.text
