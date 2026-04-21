"""High-level API tests using FastAPI's TestClient.

ffmpeg/ffprobe are stubbed via monkeypatching so these can run without the
binaries installed.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import normalize as normalize_mod
from app.config import get_settings
from app.errors import FfprobeUnavailable, InvalidInput, NoVideoStream
from app.main import app
from app.probe import ProbeResult


@pytest.fixture
def client() -> TestClient:
    # Reset cached settings so size-limit mutations don't leak between tests.
    get_settings.cache_clear()
    with TestClient(app) as c:
        c.app.state.ffmpeg_ok = True
        c.app.state.ffprobe_ok = True
        yield c
    get_settings.cache_clear()


def test_healthz_ok(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_healthz_fail_when_ffmpeg_missing(client: TestClient) -> None:
    client.app.state.ffmpeg_ok = False
    r = client.get("/healthz")
    assert r.status_code == 503
    assert r.json()["error"] == "ffmpeg_unavailable"


def test_healthz_fail_when_ffprobe_missing(client: TestClient) -> None:
    client.app.state.ffprobe_ok = False
    r = client.get("/healthz")
    assert r.status_code == 503
    assert r.json()["error"] == "ffprobe_unavailable"


def test_metrics_endpoint(client: TestClient) -> None:
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "normalize_jobs_total" in r.text


def test_normalize_success_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Stub ffprobe+ffmpeg; verify the contract: 200, video/mp4, X-* headers."""
    fake_probe = ProbeResult(
        format_name="avi",
        duration=3.0,
        has_video=True,
        has_audio=True,
        video_codec="mpeg4",
        audio_codec="mp3",
        width=640,
        height=480,
        rotation=0,
    )

    async def fake_ffprobe(path, ffprobe_path):
        return fake_probe

    async def fake_run_ffmpeg(cmd, timeout_s):
        # Emulate ffmpeg: write a small file to the output path (last argv).
        Path(cmd[-1]).write_bytes(b"FAKE-MP4-BYTES")
        return 0, ""

    monkeypatch.setattr(normalize_mod, "ffprobe", fake_ffprobe)
    monkeypatch.setattr(normalize_mod, "_run_ffmpeg", fake_run_ffmpeg)

    files = {"file": ("clip.avi", b"RIFFFAKEAVI", "video/x-msvideo")}
    r = client.post("/v1/normalize", files=files)

    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("video/mp4")
    assert r.headers["x-normalize-input-codec"] == "mpeg4"
    assert r.headers["x-normalize-remuxed"] == "0"
    assert int(r.headers["x-normalize-duration-ms"]) >= 0
    assert r.content == b"FAKE-MP4-BYTES"


def test_normalize_no_video_returns_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_ffprobe(path, ffprobe_path):
        raise NoVideoStream()

    monkeypatch.setattr(normalize_mod, "ffprobe", fake_ffprobe)

    files = {"file": ("clip.mp4", b"not-a-video", "video/mp4")}
    r = client.post("/v1/normalize", files=files)

    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "no_video_stream"


def test_normalize_invalid_input_returns_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_ffprobe(path, ffprobe_path):
        raise InvalidInput("not a media file")

    monkeypatch.setattr(normalize_mod, "ffprobe", fake_ffprobe)

    files = {"file": ("fake.mp4", b"hello world", "video/mp4")}
    r = client.post("/v1/normalize", files=files)

    assert r.status_code == 422
    assert r.json()["error"] == "invalid_input"


def test_normalize_ffprobe_infra_failure_returns_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Broken ffprobe binary (e.g. missing libavdevice.so.61) must surface as
    503 so the client's wasm fallback kicks in — not 422 (which would have the
    client blame the user's file)."""

    async def fake_ffprobe(path, ffprobe_path):
        raise FfprobeUnavailable(
            "ffprobe: error while loading shared libraries: libavdevice.so.61"
        )

    monkeypatch.setattr(normalize_mod, "ffprobe", fake_ffprobe)

    files = {"file": ("clip.mkv", b"MKVFAKE", "video/x-matroska")}
    r = client.post("/v1/normalize", files=files)

    assert r.status_code == 503
    assert r.json()["error"] == "ffprobe_unavailable"


def test_upload_size_limit_enforced_during_stream(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Shrink the limit so a small payload trips it.
    client.app.state.settings.max_upload_bytes = 16

    files = {"file": ("clip.avi", b"0" * 1024, "video/x-msvideo")}
    r = client.post("/v1/normalize", files=files)

    assert r.status_code == 413
    assert r.json()["error"] == "upload_too_large"


def test_remux_flag_header_when_already_compatible(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_probe = ProbeResult(
        format_name="mov,mp4,m4a,3gp,3g2,mj2",
        duration=3.0,
        has_video=True,
        has_audio=True,
        video_codec="h264",
        audio_codec="aac",
        width=1280,
        height=720,
        rotation=0,
    )

    async def fake_ffprobe(path, ffprobe_path):
        return fake_probe

    async def fake_run_ffmpeg(cmd, timeout_s):
        Path(cmd[-1]).write_bytes(b"remuxed")
        return 0, ""

    monkeypatch.setattr(normalize_mod, "ffprobe", fake_ffprobe)
    monkeypatch.setattr(normalize_mod, "_run_ffmpeg", fake_run_ffmpeg)

    files = {"file": ("good.mp4", b"doesnt-matter", "video/mp4")}
    r = client.post("/v1/normalize", files=files)

    assert r.status_code == 200
    assert r.headers["x-normalize-remuxed"] == "1"
