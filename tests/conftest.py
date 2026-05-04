"""Shared fixtures for the stateful API tests.

- A fresh in-memory SQLite DB per test (engine recreated → migrations re-run).
- A moto-mocked S3 endpoint per test (so S3BlobStore is exercised end-to-end).
- A fully bootstrapped FastAPI app whose lifespan never touches the network.
"""
from __future__ import annotations

import os
import socket
import threading
from typing import AsyncIterator, Iterator

import pytest
from moto.server import ThreadedMotoServer

from app.config import get_settings
from app.storage import blobs as blobs_mod
from app.storage import db as db_mod


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def moto_server() -> Iterator[str]:
    """Run a real S3-compatible HTTP server (moto) for the whole test
    session. Each test bootstraps its own bucket against the same endpoint.
    """
    port = _free_port()
    server = ThreadedMotoServer(port=port)
    server.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.stop()


@pytest.fixture
def stateful_env(monkeypatch: pytest.MonkeyPatch, tmp_path, moto_server: str) -> dict[str, str]:
    """Set the env vars that Settings reads, then clear the lru_cache so
    they actually take effect on the next get_settings() call.
    """
    bucket = "videonizer-test"
    db_path = tmp_path / "test.sqlite3"
    (tmp_path / "tmp").mkdir(parents=True, exist_ok=True)
    env = {
        # File-backed sqlite so the alembic engine and the app engine see
        # the same data (`:memory:` would isolate them per-connection).
        "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
        "S3_ENDPOINT": moto_server,
        "S3_REGION": "us-east-1",
        "S3_BUCKET": bucket,
        "S3_ACCESS_KEY": "test",
        "S3_SECRET_KEY": "test",
        "S3_FORCE_PATH_STYLE": "true",
        "DATA_DIR": str(tmp_path),
        "TEMP_DIR": str(tmp_path / "tmp"),
        "AUTO_MIGRATE": "true",
        "ALLOWED_ORIGINS": "",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    yield env
    get_settings.cache_clear()


@pytest.fixture
async def stateful_app(stateful_env):
    """Boot the FastAPI app with stateful infra wired (sqlite + moto S3).

    Yields a httpx.AsyncClient targeting an ASGI transport (no TCP).
    Tests can also reach into ``app.state`` if needed.
    """
    import httpx

    from app.main import app

    # Pretend ffmpeg is installed so /healthz reports OK; legacy paths
    # aren't exercised by stateful tests, but lifespan probes them.
    from unittest.mock import patch

    async def _fake_check(_path: str) -> bool:
        return True

    with patch("app.main._check_binary", _fake_check):
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                yield client


@pytest.fixture
async def blob_store(stateful_app):
    """The lifespan-installed BlobStore singleton."""
    return blobs_mod.get_blob_store()


@pytest.fixture
async def session_factory(stateful_app):
    return db_mod.get_sessionmaker()
