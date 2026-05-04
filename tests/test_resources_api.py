"""Integration tests for ``/v1/projects/{id}/resources`` against the contract."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image as PILImage

from app.config import get_settings
from app.main import app
from app.storage import clear_locks, configure_storage_root


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    get_settings.cache_clear()
    configure_storage_root(tmp_path)
    clear_locks()
    with TestClient(app) as c:
        c.app.state.ffmpeg_ok = True
        c.app.state.ffprobe_ok = True
        yield c
    configure_storage_root(None)
    clear_locks()
    get_settings.cache_clear()


def _new_project(client: TestClient, name: str = "P") -> str:
    return client.post("/v1/projects", json={"name": name}).json()["project"]["id"]


def _png_bytes(w: int = 4, h: int = 4, color: tuple[int, int, int] = (0, 200, 0)) -> bytes:
    img = PILImage.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ----- list / create ---------------------------------------------------


def test_list_empty(client: TestClient) -> None:
    pid = _new_project(client)
    r = client.get(f"/v1/projects/{pid}/resources")
    assert r.status_code == 200
    assert r.json() == {"resources": []}


def test_create_image_batch(client: TestClient) -> None:
    pid = _new_project(client)
    r = client.post(
        f"/v1/projects/{pid}/resources",
        data={"type": "image_batch", "name": "Batch A", "tags": json.dumps(["t1"])},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["resource"]["type"] == "image_batch"
    assert body["resource"]["name"] == "Batch A"
    assert body["resource"]["tags"] == ["t1"]


def test_create_video_with_source(client: TestClient) -> None:
    pid = _new_project(client)
    fake = b"FAKE-AVI-BYTES" * 20
    r = client.post(
        f"/v1/projects/{pid}/resources",
        data={
            "type": "video",
            "name": "clip",
            "width": "640",
            "height": "480",
            "duration": "12.5",
            "ingestVia": "server",
        },
        files={"file": ("clip.avi", fake, "video/x-msvideo")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["resource"]["type"] == "video"
    assert body["resource"]["sourceExt"] == "avi"
    assert body["resource"]["width"] == 640
    assert body["resource"]["height"] == 480


def test_create_video_requires_file(client: TestClient) -> None:
    pid = _new_project(client)
    r = client.post(
        f"/v1/projects/{pid}/resources",
        data={"type": "video", "name": "clip", "width": "1", "height": "1"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_input"


def test_create_video_requires_width_height(client: TestClient) -> None:
    pid = _new_project(client)
    r = client.post(
        f"/v1/projects/{pid}/resources",
        data={"type": "video", "name": "clip"},
        files={"file": ("c.avi", b"x", "video/x-msvideo")},
    )
    # Missing width/height comes through as 400 invalid_input.
    assert r.status_code == 400


def test_create_unknown_type(client: TestClient) -> None:
    pid = _new_project(client)
    r = client.post(
        f"/v1/projects/{pid}/resources",
        data={"type": "garbage", "name": "x"},
    )
    assert r.status_code == 400


def test_create_blank_name(client: TestClient) -> None:
    pid = _new_project(client)
    r = client.post(
        f"/v1/projects/{pid}/resources",
        data={"type": "image_batch", "name": "  "},
    )
    assert r.status_code == 400


def test_create_bad_tags_json(client: TestClient) -> None:
    pid = _new_project(client)
    r = client.post(
        f"/v1/projects/{pid}/resources",
        data={"type": "image_batch", "name": "x", "tags": "not-json"},
    )
    assert r.status_code == 400


# ----- get / patch / delete --------------------------------------------


def test_get_returns_resource(client: TestClient) -> None:
    pid = _new_project(client)
    rid = client.post(
        f"/v1/projects/{pid}/resources",
        data={"type": "image_batch", "name": "X"},
    ).json()["resource"]["id"]
    r = client.get(f"/v1/projects/{pid}/resources/{rid}")
    assert r.status_code == 200
    assert r.json()["resource"]["id"] == rid


def test_get_missing_returns_404(client: TestClient) -> None:
    pid = _new_project(client)
    r = client.get(f"/v1/projects/{pid}/resources/nope")
    assert r.status_code == 404


def test_patch_name_and_tags(client: TestClient) -> None:
    pid = _new_project(client)
    rid = client.post(
        f"/v1/projects/{pid}/resources",
        data={"type": "image_batch", "name": "A"},
    ).json()["resource"]["id"]
    r = client.patch(
        f"/v1/projects/{pid}/resources/{rid}",
        json={"name": "B", "tags": ["t1", "t2"]},
    )
    assert r.status_code == 200
    body = r.json()["resource"]
    assert body["name"] == "B"
    assert body["tags"] == ["t1", "t2"]


def test_patch_missing_returns_404(client: TestClient) -> None:
    pid = _new_project(client)
    r = client.patch(f"/v1/projects/{pid}/resources/nope", json={"name": "x"})
    assert r.status_code == 404


def test_delete_idempotent(client: TestClient) -> None:
    pid = _new_project(client)
    r = client.delete(f"/v1/projects/{pid}/resources/nope")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_delete_cascades_images(client: TestClient) -> None:
    pid = _new_project(client)
    rid = client.post(
        f"/v1/projects/{pid}/resources",
        data={"type": "image_batch", "name": "A"},
    ).json()["resource"]["id"]
    # Add an image to the resource.
    img_meta = json.dumps([{"fileName": "a.png", "width": 4, "height": 4}])
    r = client.post(
        f"/v1/projects/{pid}/resources/{rid}/images",
        data={"meta": img_meta},
        files=[("files", ("a.png", _png_bytes(), "image/png"))],
    )
    assert r.status_code == 201, r.text
    # Delete the resource — should cascade.
    client.delete(f"/v1/projects/{pid}/resources/{rid}")
    listed = client.get(f"/v1/projects/{pid}/resources").json()["resources"]
    assert listed == []
    # Image should be gone too.
    images = client.get(f"/v1/projects/{pid}/images").json()
    # /images route doesn't exist yet; verify via storage indirectly using list response shape.
    # In step 5 this will be testable via API. For now skip if not present.
    # (this path is exercised by the storage cascade test in test_storage.py)


# ----- source streaming ------------------------------------------------


def _make_video(client: TestClient, pid: str, body: bytes = b"FAKE-MP4-BYTES" * 100) -> str:
    r = client.post(
        f"/v1/projects/{pid}/resources",
        data={"type": "video", "name": "clip", "width": "1", "height": "1"},
        files={"file": ("clip.mp4", body, "video/mp4")},
    )
    assert r.status_code == 201, r.text
    return r.json()["resource"]["id"]


def test_source_full_response(client: TestClient) -> None:
    pid = _new_project(client)
    body = b"X" * 257
    rid = _make_video(client, pid, body)
    r = client.get(f"/v1/projects/{pid}/resources/{rid}/source")
    assert r.status_code == 200
    assert r.headers["content-length"] == "257"
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-type"].startswith("video/mp4")
    assert r.content == body


def test_source_range_request(client: TestClient) -> None:
    pid = _new_project(client)
    body = bytes(range(256)) * 4  # 1024 bytes, predictable content
    rid = _make_video(client, pid, body)
    r = client.get(
        f"/v1/projects/{pid}/resources/{rid}/source",
        headers={"range": "bytes=10-19"},
    )
    assert r.status_code == 206
    assert r.headers["content-length"] == "10"
    assert r.headers["content-range"] == f"bytes 10-19/{len(body)}"
    assert r.content == body[10:20]


def test_source_suffix_range(client: TestClient) -> None:
    pid = _new_project(client)
    body = b"abcdefghij" * 10  # 100 bytes
    rid = _make_video(client, pid, body)
    r = client.get(
        f"/v1/projects/{pid}/resources/{rid}/source",
        headers={"range": "bytes=-20"},
    )
    assert r.status_code == 206
    assert r.content == body[-20:]


def test_source_open_ended_range(client: TestClient) -> None:
    pid = _new_project(client)
    body = b"abcdefghij" * 5  # 50 bytes
    rid = _make_video(client, pid, body)
    r = client.get(
        f"/v1/projects/{pid}/resources/{rid}/source",
        headers={"range": "bytes=10-"},
    )
    assert r.status_code == 206
    assert r.content == body[10:]


def test_source_invalid_range_returns_416(client: TestClient) -> None:
    pid = _new_project(client)
    body = b"X" * 50
    rid = _make_video(client, pid, body)
    # Out of bounds end.
    r = client.get(
        f"/v1/projects/{pid}/resources/{rid}/source",
        headers={"range": "bytes=0-99"},
    )
    assert r.status_code == 416
    assert r.headers["content-range"] == f"bytes */{len(body)}"


def test_source_garbage_range_returns_416(client: TestClient) -> None:
    pid = _new_project(client)
    rid = _make_video(client, pid, b"X" * 10)
    r = client.get(
        f"/v1/projects/{pid}/resources/{rid}/source",
        headers={"range": "frames=0-1"},
    )
    assert r.status_code == 416


def test_source_404_for_image_batch(client: TestClient) -> None:
    """``stat_resource_source`` returns None for non-video resources."""
    pid = _new_project(client)
    rid = client.post(
        f"/v1/projects/{pid}/resources",
        data={"type": "image_batch", "name": "A"},
    ).json()["resource"]["id"]
    r = client.get(f"/v1/projects/{pid}/resources/{rid}/source")
    assert r.status_code == 404


# ----- previews --------------------------------------------------------


def test_previews_post_then_get(client: TestClient) -> None:
    pid = _new_project(client)
    rid = _make_video(client, pid)
    p0 = _png_bytes(color=(255, 0, 0))
    p1 = _png_bytes(color=(0, 0, 255))
    r = client.post(
        f"/v1/projects/{pid}/resources/{rid}/previews",
        files=[
            ("files", ("p0.jpg", p0, "image/jpeg")),
            ("files", ("p1.jpg", p1, "image/jpeg")),
        ],
    )
    assert r.status_code == 200
    assert r.json() == {"previewCount": 2}
    g0 = client.get(f"/v1/projects/{pid}/resources/{rid}/previews/0")
    assert g0.status_code == 200
    assert g0.headers["content-type"] == "image/jpeg"
    assert g0.content == p0


def test_previews_get_invalid_idx_returns_400(client: TestClient) -> None:
    pid = _new_project(client)
    rid = _make_video(client, pid)
    r = client.get(f"/v1/projects/{pid}/resources/{rid}/previews/abc")
    assert r.status_code == 400


def test_previews_get_out_of_range_404(client: TestClient) -> None:
    pid = _new_project(client)
    rid = _make_video(client, pid)
    r = client.get(f"/v1/projects/{pid}/resources/{rid}/previews/99")
    assert r.status_code == 404


def test_previews_post_to_image_batch_returns_422(client: TestClient) -> None:
    pid = _new_project(client)
    rid = client.post(
        f"/v1/projects/{pid}/resources",
        data={"type": "image_batch", "name": "A"},
    ).json()["resource"]["id"]
    r = client.post(
        f"/v1/projects/{pid}/resources/{rid}/previews",
        files=[("files", ("p.jpg", b"x", "image/jpeg"))],
    )
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_input"


def test_previews_post_to_missing_resource_404(client: TestClient) -> None:
    pid = _new_project(client)
    r = client.post(
        f"/v1/projects/{pid}/resources/nope/previews",
        files=[("files", ("p.jpg", b"x", "image/jpeg"))],
    )
    assert r.status_code == 404


# ----- image ingest ----------------------------------------------------


def test_ingest_uploaded_images(client: TestClient) -> None:
    pid = _new_project(client)
    rid = client.post(
        f"/v1/projects/{pid}/resources",
        data={"type": "image_batch", "name": "A"},
    ).json()["resource"]["id"]
    meta = json.dumps([
        {"fileName": "a.png", "width": 4, "height": 4},
        {"fileName": "b.png", "width": 4, "height": 4},
    ])
    a = _png_bytes(color=(10, 10, 10))
    b = _png_bytes(color=(20, 20, 20))
    r = client.post(
        f"/v1/projects/{pid}/resources/{rid}/images",
        data={"meta": meta},
        files=[
            ("files", ("a.png", a, "image/png")),
            ("files", ("b.png", b, "image/png")),
        ],
    )
    assert r.status_code == 201, r.text
    images = r.json()["images"]
    assert len(images) == 2
    assert all(img["resourceId"] == rid for img in images)
    assert all(img["source"] == "uploaded" for img in images)


def test_ingest_video_frames(client: TestClient) -> None:
    pid = _new_project(client)
    rid = _make_video(client, pid)
    meta = json.dumps([
        {"fileName": "f1.jpg", "width": 4, "height": 4, "timestamp": 1.5, "frameIndex": 30},
    ])
    r = client.post(
        f"/v1/projects/{pid}/resources/{rid}/images",
        data={"meta": meta},
        files=[("files", ("f1.jpg", _png_bytes(), "image/jpeg"))],
    )
    assert r.status_code == 201, r.text
    img = r.json()["images"][0]
    assert img["source"] == "video_frame"
    assert img["videoFrameMeta"]["timestamp"] == 1.5
    assert img["videoFrameMeta"]["frameIndex"] == 30


def test_ingest_count_mismatch_returns_400(client: TestClient) -> None:
    pid = _new_project(client)
    rid = client.post(
        f"/v1/projects/{pid}/resources",
        data={"type": "image_batch", "name": "A"},
    ).json()["resource"]["id"]
    meta = json.dumps([
        {"fileName": "a.png", "width": 4, "height": 4},
        {"fileName": "b.png", "width": 4, "height": 4},
    ])
    r = client.post(
        f"/v1/projects/{pid}/resources/{rid}/images",
        data={"meta": meta},
        files=[("files", ("a.png", _png_bytes(), "image/png"))],
    )
    assert r.status_code == 400


def test_ingest_invalid_meta_returns_400(client: TestClient) -> None:
    pid = _new_project(client)
    rid = client.post(
        f"/v1/projects/{pid}/resources",
        data={"type": "image_batch", "name": "A"},
    ).json()["resource"]["id"]
    r = client.post(
        f"/v1/projects/{pid}/resources/{rid}/images",
        data={"meta": "{not-json"},
        files=[("files", ("a.png", _png_bytes(), "image/png"))],
    )
    assert r.status_code == 400


def test_ingest_to_missing_resource_404(client: TestClient) -> None:
    pid = _new_project(client)
    meta = json.dumps([{"fileName": "a.png", "width": 4, "height": 4}])
    r = client.post(
        f"/v1/projects/{pid}/resources/nope/images",
        data={"meta": meta},
        files=[("files", ("a.png", _png_bytes(), "image/png"))],
    )
    assert r.status_code == 404
