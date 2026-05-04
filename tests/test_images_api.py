"""Integration tests for ``/v1/projects/{id}/images`` against the contract."""
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


def _png(w: int = 8, h: int = 8, color: tuple[int, int, int] = (10, 10, 10)) -> bytes:
    img = PILImage.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _setup_project_with_images(client: TestClient, n: int = 2) -> tuple[str, str, list[dict]]:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    rid = client.post(
        f"/v1/projects/{pid}/resources",
        data={"type": "image_batch", "name": "A"},
    ).json()["resource"]["id"]
    metas = [{"fileName": f"img{i}.png", "width": 8, "height": 8} for i in range(n)]
    files = [
        ("files", (f"img{i}.png", _png(color=(i * 30, 0, 0)), "image/png"))
        for i in range(n)
    ]
    r = client.post(
        f"/v1/projects/{pid}/resources/{rid}/images",
        data={"meta": json.dumps(metas)},
        files=files,
    )
    assert r.status_code == 201
    return pid, rid, r.json()["images"]


# ----- list / get / patch / delete ------------------------------------


def test_list_empty(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    r = client.get(f"/v1/projects/{pid}/images")
    assert r.status_code == 200
    assert r.json() == {"images": []}


def test_list_returns_images(client: TestClient) -> None:
    pid, _rid, imgs = _setup_project_with_images(client, n=3)
    r = client.get(f"/v1/projects/{pid}/images")
    assert r.status_code == 200
    listed = r.json()["images"]
    assert {i["id"] for i in listed} == {i["id"] for i in imgs}


def test_list_filter_by_resource(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    r1 = client.post(
        f"/v1/projects/{pid}/resources",
        data={"type": "image_batch", "name": "A"},
    ).json()["resource"]["id"]
    r2 = client.post(
        f"/v1/projects/{pid}/resources",
        data={"type": "image_batch", "name": "B"},
    ).json()["resource"]["id"]

    def add(rid: str, name: str) -> dict:
        return client.post(
            f"/v1/projects/{pid}/resources/{rid}/images",
            data={"meta": json.dumps([{"fileName": name, "width": 4, "height": 4}])},
            files=[("files", (name, _png(), "image/png"))],
        ).json()["images"][0]

    a = add(r1, "a.png")
    b = add(r2, "b.png")
    listed = client.get(f"/v1/projects/{pid}/images?resourceId={r1}").json()["images"]
    assert [i["id"] for i in listed] == [a["id"]]


def test_list_filter_by_tag(client: TestClient) -> None:
    pid, _rid, imgs = _setup_project_with_images(client, n=2)
    # Tag the first image only.
    client.patch(
        f"/v1/projects/{pid}/images/{imgs[0]['id']}",
        json={"tags": ["needle"]},
    )
    listed = client.get(f"/v1/projects/{pid}/images?tag=needle").json()["images"]
    assert [i["id"] for i in listed] == [imgs[0]["id"]]


def test_list_filter_by_source(client: TestClient) -> None:
    pid, _rid, _imgs = _setup_project_with_images(client, n=1)
    listed = client.get(f"/v1/projects/{pid}/images?source=uploaded").json()["images"]
    assert len(listed) == 1
    listed = client.get(f"/v1/projects/{pid}/images?source=video_frame").json()["images"]
    assert listed == []


def test_get_existing(client: TestClient) -> None:
    pid, _rid, imgs = _setup_project_with_images(client, n=1)
    r = client.get(f"/v1/projects/{pid}/images/{imgs[0]['id']}")
    assert r.status_code == 200
    assert r.json()["image"]["id"] == imgs[0]["id"]


def test_get_missing_returns_404(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    r = client.get(f"/v1/projects/{pid}/images/nope")
    assert r.status_code == 404


def test_patch_replaces_tags(client: TestClient) -> None:
    pid, _rid, imgs = _setup_project_with_images(client, n=1)
    iid = imgs[0]["id"]
    r = client.patch(
        f"/v1/projects/{pid}/images/{iid}",
        json={"tags": ["t1", "t2"]},
    )
    assert r.status_code == 200
    assert r.json()["image"]["tags"] == ["t1", "t2"]


def test_patch_missing_returns_404(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    r = client.patch(f"/v1/projects/{pid}/images/nope", json={"tags": ["x"]})
    assert r.status_code == 404


def test_delete_idempotent(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    r = client.delete(f"/v1/projects/{pid}/images/nope")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_delete_existing(client: TestClient) -> None:
    pid, _rid, imgs = _setup_project_with_images(client, n=1)
    iid = imgs[0]["id"]
    r = client.delete(f"/v1/projects/{pid}/images/{iid}")
    assert r.status_code == 200
    assert client.get(f"/v1/projects/{pid}/images/{iid}").status_code == 404


# ----- bytes / thumb ---------------------------------------------------


def test_bytes_returns_original(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    rid = client.post(
        f"/v1/projects/{pid}/resources",
        data={"type": "image_batch", "name": "A"},
    ).json()["resource"]["id"]
    raw = _png(color=(123, 45, 67))
    img = client.post(
        f"/v1/projects/{pid}/resources/{rid}/images",
        data={"meta": json.dumps([{"fileName": "x.png", "width": 8, "height": 8}])},
        files=[("files", ("x.png", raw, "image/png"))],
    ).json()["images"][0]
    r = client.get(f"/v1/projects/{pid}/images/{img['id']}/bytes")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert r.content == raw


def test_bytes_missing_returns_404(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    r = client.get(f"/v1/projects/{pid}/images/nope/bytes")
    assert r.status_code == 404


def test_thumb_returns_jpeg(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    rid = client.post(
        f"/v1/projects/{pid}/resources",
        data={"type": "image_batch", "name": "A"},
    ).json()["resource"]["id"]
    big = _png(w=1024, h=768, color=(255, 0, 0))
    img = client.post(
        f"/v1/projects/{pid}/resources/{rid}/images",
        data={"meta": json.dumps([{"fileName": "big.png", "width": 1024, "height": 768}])},
        files=[("files", ("big.png", big, "image/png"))],
    ).json()["images"][0]
    r = client.get(f"/v1/projects/{pid}/images/{img['id']}/thumb")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content[:3] == b"\xff\xd8\xff"  # JPEG SOI
    # Second hit serves the cached thumb.
    r2 = client.get(f"/v1/projects/{pid}/images/{img['id']}/thumb")
    assert r2.content == r.content


def test_thumb_missing_returns_404(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    r = client.get(f"/v1/projects/{pid}/images/nope/thumb")
    assert r.status_code == 404


# ----- bulk tag --------------------------------------------------------


def test_bulk_tag_add(client: TestClient) -> None:
    pid, _rid, imgs = _setup_project_with_images(client, n=2)
    r = client.post(
        f"/v1/projects/{pid}/images/tags",
        json={"imageIds": [i["id"] for i in imgs], "tags": ["t1"], "mode": "add"},
    )
    assert r.status_code == 200
    assert r.json() == {"updated": 2}
    a = client.get(f"/v1/projects/{pid}/images/{imgs[0]['id']}").json()["image"]
    assert "t1" in a["tags"]


def test_bulk_tag_replace(client: TestClient) -> None:
    pid, _rid, imgs = _setup_project_with_images(client, n=1)
    client.patch(
        f"/v1/projects/{pid}/images/{imgs[0]['id']}",
        json={"tags": ["old"]},
    )
    client.post(
        f"/v1/projects/{pid}/images/tags",
        json={"imageIds": [imgs[0]["id"]], "tags": ["new"], "mode": "replace"},
    )
    a = client.get(f"/v1/projects/{pid}/images/{imgs[0]['id']}").json()["image"]
    assert a["tags"] == ["new"]


def test_bulk_tag_remove(client: TestClient) -> None:
    pid, _rid, imgs = _setup_project_with_images(client, n=1)
    client.patch(
        f"/v1/projects/{pid}/images/{imgs[0]['id']}",
        json={"tags": ["a", "b"]},
    )
    client.post(
        f"/v1/projects/{pid}/images/tags",
        json={"imageIds": [imgs[0]["id"]], "tags": ["a"], "mode": "remove"},
    )
    a = client.get(f"/v1/projects/{pid}/images/{imgs[0]['id']}").json()["image"]
    assert a["tags"] == ["b"]


def test_bulk_tag_empty_ids_returns_zero(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    r = client.post(
        f"/v1/projects/{pid}/images/tags",
        json={"imageIds": [], "tags": ["t"], "mode": "add"},
    )
    assert r.status_code == 200
    assert r.json() == {"updated": 0}


def test_bulk_tag_default_mode_is_add(client: TestClient) -> None:
    pid, _rid, imgs = _setup_project_with_images(client, n=1)
    client.patch(
        f"/v1/projects/{pid}/images/{imgs[0]['id']}",
        json={"tags": ["existing"]},
    )
    client.post(
        f"/v1/projects/{pid}/images/tags",
        json={"imageIds": [imgs[0]["id"]], "tags": ["new"]},
    )
    a = client.get(f"/v1/projects/{pid}/images/{imgs[0]['id']}").json()["image"]
    assert set(a["tags"]) == {"existing", "new"}


def test_bulk_tag_strips_and_drops_empty_tags(client: TestClient) -> None:
    """Match vision: tags are trimmed and empty strings dropped."""
    pid, _rid, imgs = _setup_project_with_images(client, n=1)
    client.post(
        f"/v1/projects/{pid}/images/tags",
        json={"imageIds": [imgs[0]["id"]], "tags": ["  hello  ", "", "world"], "mode": "replace"},
    )
    a = client.get(f"/v1/projects/{pid}/images/{imgs[0]['id']}").json()["image"]
    assert a["tags"] == ["hello", "world"]
