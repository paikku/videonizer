"""Tests for /v1/segment.

The actual model backends (FastSAM / Mask R-CNN) are NOT exercised here —
they pull torch + ultralytics + auto-download weights, none of which we
want in the unit-test loop. Instead we install a fake backend through the
service-level `resolve` injection point and verify:

  - request validation (model enum, region JSON, file required)
  - happy path: image bytes -> mask -> polygon rings in the response
  - empty-mask -> 200 {} (no-op contract)
  - polygon utilities (mask shape -> rings, holes preserved)

Backends themselves are smoke-tested separately when their deps are
present (see `test_segment_backends.py` — gated on import).
"""
from __future__ import annotations

import io
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import segment as segment_pkg
from app.config import get_settings
from app.errors import SegmentInvalidRegion, SegmentUnsupportedModel
from app.main import app
from app.segment.backends.base import BackendResult, RegionPx
from app.segment.polygon import mask_to_polygon, rings_aabb
from app.segment.registry import (
    ResolvedBackend,
    _ROUTING,
    install_test_backend,
    reset_default_registry,
)
from app.segment.service import parse_region, segment_image, validate_model


# ---------------------------------------------------------------- fixtures


def _png_bytes(width: int = 64, height: int = 64) -> bytes:
    """Generate a tiny in-memory PNG so we can hit the decode path."""
    img = Image.new("RGB", (width, height), color=(80, 120, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _FakeBackend:
    """Returns a deterministic mask with a square hole — easy to assert on."""

    name = "fake"

    def __init__(self, return_none: bool = False) -> None:
        self._return_none = return_none

    def is_available(self) -> bool:
        return True

    def infer(self, image, region: RegionPx, *, class_hint: str | None = None):
        if self._return_none:
            return None
        h, w = image.shape[:2]
        mask = np.zeros((h, w), dtype=np.uint8)
        # Outer square fills the crop.
        mask[1 : h - 1, 1 : w - 1] = 1
        # Hole in the middle.
        if h > 8 and w > 8:
            mask[h // 4 : 3 * h // 4, w // 4 : 3 * w // 4] = 0
        return BackendResult(mask=mask, score=0.87)


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_default_registry()
    # Snapshot routing so install_test_backend doesn't bleed across tests.
    snapshot = dict(_ROUTING)
    yield
    _ROUTING.clear()
    _ROUTING.update(snapshot)
    reset_default_registry()


@pytest.fixture
def client() -> TestClient:
    get_settings.cache_clear()
    with TestClient(app) as c:
        c.app.state.ffmpeg_ok = True
        c.app.state.ffprobe_ok = True
        yield c
    get_settings.cache_clear()


# ----------------------------------------------------- validation unit tests


def test_validate_model_default_when_missing():
    assert validate_model(None) == "sam3"
    assert validate_model("") == "sam3"


def test_validate_model_rejects_unknown():
    with pytest.raises(SegmentUnsupportedModel):
        validate_model("yolo")


def test_parse_region_happy():
    assert parse_region('{"x":0.1,"y":0.2,"w":0.3,"h":0.4}') == (0.1, 0.2, 0.3, 0.4)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not-json",
        "[]",
        '{"x":0.1}',
        '{"x":-0.1,"y":0,"w":0.5,"h":0.5}',
        '{"x":0,"y":0,"w":0,"h":0.5}',
        '{"x":0.6,"y":0,"w":0.6,"h":0.5}',
    ],
)
def test_parse_region_rejects_invalid(raw: str):
    with pytest.raises(SegmentInvalidRegion):
        parse_region(raw)


# ----------------------------------------------------- polygon utility


def test_mask_to_polygon_extracts_outer_and_hole():
    # 100x100 mask with a centered hole — should yield 2 rings (outer + 1 hole).
    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[10:90, 10:90] = 1
    mask[40:60, 40:60] = 0  # hole

    rings = mask_to_polygon(mask, image_width=100, image_height=100)
    assert len(rings) == 2
    outer, hole = rings
    assert len(outer) >= 4
    assert len(hole) >= 4
    # All points normalized into [0, 1].
    for ring in rings:
        for x, y in ring:
            assert 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0


def test_mask_to_polygon_empty_mask_returns_empty():
    mask = np.zeros((50, 50), dtype=np.uint8)
    assert mask_to_polygon(mask, image_width=50, image_height=50) == []


def test_rings_aabb_outer_only():
    rings = [[(0.1, 0.2), (0.5, 0.2), (0.5, 0.8), (0.1, 0.8)]]
    box = rings_aabb(rings)
    assert box is not None
    assert box["x"] == pytest.approx(0.1)
    assert box["y"] == pytest.approx(0.2)
    assert box["w"] == pytest.approx(0.4)
    assert box["h"] == pytest.approx(0.6)


# ----------------------------------------------------- service-level happy path


def test_segment_image_returns_polygon_with_fake_backend():
    backend = _FakeBackend()
    install_test_backend("sam3", backend, backend_id="fake-fastsam")

    out = segment_image(
        file_bytes=_png_bytes(),
        region_raw='{"x":0.2,"y":0.2,"w":0.6,"h":0.6}',
        model="sam3",
    )
    assert out is not None
    assert out.backend_id == "fake-fastsam"
    assert out.score == pytest.approx(0.87)
    assert len(out.polygon) >= 1
    assert out.rect is not None


def test_segment_image_returns_none_when_backend_finds_nothing():
    install_test_backend("mask-rcnn", _FakeBackend(return_none=True), backend_id="fake-mrcnn")
    out = segment_image(
        file_bytes=_png_bytes(),
        region_raw='{"x":0.2,"y":0.2,"w":0.6,"h":0.6}',
        model="mask-rcnn",
    )
    assert out is None


# ----------------------------------------------------- API route tests


def test_segment_endpoint_happy_path(client: TestClient):
    install_test_backend("sam3", _FakeBackend(), backend_id="fake-fastsam")
    files = {"file": ("frame.png", _png_bytes(), "image/png")}
    data = {
        "region": json.dumps({"x": 0.2, "y": 0.2, "w": 0.6, "h": 0.6}),
        "model": "sam3",
        "classHint": "person",
    }
    r = client.post("/v1/segment", files=files, data=data)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "polygon" in body
    assert isinstance(body["polygon"], list) and len(body["polygon"]) >= 1
    assert "rect" in body
    assert "score" in body
    assert r.headers["x-segment-backend"] == "fake-fastsam"
    assert int(r.headers["x-segment-duration-ms"]) >= 0


def test_segment_endpoint_no_object_returns_empty_200(client: TestClient):
    install_test_backend("sam3", _FakeBackend(return_none=True), backend_id="fake-fastsam")
    files = {"file": ("frame.png", _png_bytes(), "image/png")}
    data = {"region": json.dumps({"x": 0.2, "y": 0.2, "w": 0.6, "h": 0.6})}
    r = client.post("/v1/segment", files=files, data=data)
    assert r.status_code == 200
    assert r.json() == {}
    assert "x-segment-backend" not in {k.lower() for k in r.headers.keys()}


def test_segment_endpoint_rejects_unsupported_model(client: TestClient):
    files = {"file": ("frame.png", _png_bytes(), "image/png")}
    data = {
        "region": json.dumps({"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5}),
        "model": "yolo",
    }
    r = client.post("/v1/segment", files=files, data=data)
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "unsupported model"


def test_segment_endpoint_rejects_bad_region(client: TestClient):
    install_test_backend("sam3", _FakeBackend(), backend_id="fake-fastsam")
    files = {"file": ("frame.png", _png_bytes(), "image/png")}
    data = {"region": "not-a-json"}
    r = client.post("/v1/segment", files=files, data=data)
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_region"


def test_segment_endpoint_rejects_garbage_image(client: TestClient):
    install_test_backend("sam3", _FakeBackend(), backend_id="fake-fastsam")
    files = {"file": ("frame.png", b"not-an-image", "image/png")}
    data = {"region": json.dumps({"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5})}
    r = client.post("/v1/segment", files=files, data=data)
    assert r.status_code in (400, 415)
    body = r.json()
    assert body["error"] in {"image_decode_failed", "unsupported_media_type"}


def test_segment_endpoint_enforces_upload_limit(client: TestClient):
    install_test_backend("sam3", _FakeBackend(), backend_id="fake-fastsam")
    client.app.state.settings.segment_max_upload_bytes = 8
    files = {"file": ("frame.png", _png_bytes(), "image/png")}
    data = {"region": json.dumps({"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5})}
    r = client.post("/v1/segment", files=files, data=data)
    assert r.status_code == 413
    assert r.json()["error"] == "upload_too_large"


def test_segment_models_introspection_endpoint(client: TestClient):
    r = client.get("/v1/segment/models")
    assert r.status_code == 200
    body = r.json()
    assert body["default"] == "sam3"
    ids = [m["id"] for m in body["models"]]
    assert set(ids) >= {"sam3", "sam2", "sam", "mask2former", "mask-rcnn"}
    # Each entry surfaces the actual backend name (i.e. what really ran).
    for entry in body["models"]:
        assert entry["backend"]


# Silence unused-import warnings when running this module standalone.
_ = (segment_pkg, ResolvedBackend)
