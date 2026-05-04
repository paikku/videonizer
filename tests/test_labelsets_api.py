"""Integration tests for ``/v1/projects/{id}/labelsets`` (CRUD + annotations)."""
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


def _png() -> bytes:
    img = PILImage.new("RGB", (4, 4), (10, 10, 10))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _setup_with_image(client: TestClient) -> tuple[str, str]:
    """Create project + image_batch resource + 1 image. Return (pid, iid)."""
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    rid = client.post(
        f"/v1/projects/{pid}/resources",
        data={"type": "image_batch", "name": "A"},
    ).json()["resource"]["id"]
    img = client.post(
        f"/v1/projects/{pid}/resources/{rid}/images",
        data={"meta": json.dumps([{"fileName": "a.png", "width": 4, "height": 4}])},
        files=[("files", ("a.png", _png(), "image/png"))],
    ).json()["images"][0]
    return pid, img["id"]


# ----- list / create ---------------------------------------------------


def test_list_empty(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    r = client.get(f"/v1/projects/{pid}/labelsets")
    assert r.status_code == 200
    assert r.json() == {"labelsets": []}


def test_create_minimal(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    r = client.post(
        f"/v1/projects/{pid}/labelsets",
        json={"name": "L", "type": "bbox"},
    )
    assert r.status_code == 201
    body = r.json()["labelset"]
    assert body["name"] == "L"
    assert body["type"] == "bbox"
    assert body["imageIds"] == []
    assert body["excludedImageIds"] == []
    assert body["classes"] == []


def test_create_with_image_ids(client: TestClient) -> None:
    pid, iid = _setup_with_image(client)
    r = client.post(
        f"/v1/projects/{pid}/labelsets",
        json={"name": "L", "type": "polygon", "imageIds": [iid]},
    )
    assert r.status_code == 201
    assert r.json()["labelset"]["imageIds"] == [iid]


def test_create_blank_name(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    r = client.post(
        f"/v1/projects/{pid}/labelsets",
        json={"name": "  ", "type": "bbox"},
    )
    assert r.status_code == 400


def test_create_invalid_type(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    r = client.post(
        f"/v1/projects/{pid}/labelsets",
        json={"name": "L", "type": "garbage"},
    )
    assert r.status_code == 400


def test_create_missing_type_returns_422(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    r = client.post(f"/v1/projects/{pid}/labelsets", json={"name": "L"})
    assert r.status_code == 422


# ----- get / patch / delete --------------------------------------------


def test_get_existing(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    lsid = client.post(
        f"/v1/projects/{pid}/labelsets",
        json={"name": "L", "type": "bbox"},
    ).json()["labelset"]["id"]
    r = client.get(f"/v1/projects/{pid}/labelsets/{lsid}")
    assert r.status_code == 200
    assert r.json()["labelset"]["id"] == lsid


def test_get_missing_returns_404(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    r = client.get(f"/v1/projects/{pid}/labelsets/nope")
    assert r.status_code == 404


def test_patch_all_fields(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    lsid = client.post(
        f"/v1/projects/{pid}/labelsets",
        json={"name": "L", "type": "bbox"},
    ).json()["labelset"]["id"]
    r = client.patch(
        f"/v1/projects/{pid}/labelsets/{lsid}",
        json={
            "name": "L2",
            "description": "desc",
            "classes": [{"id": "c1", "name": "x", "color": "#fff"}],
            "imageIds": ["i1", "i2"],
            "excludedImageIds": ["i3"],
        },
    )
    assert r.status_code == 200
    body = r.json()["labelset"]
    assert body["name"] == "L2"
    assert body["description"] == "desc"
    assert body["classes"] == [{"id": "c1", "name": "x", "color": "#fff"}]
    assert body["imageIds"] == ["i1", "i2"]
    assert body["excludedImageIds"] == ["i3"]


def test_patch_missing_returns_404(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    r = client.patch(f"/v1/projects/{pid}/labelsets/nope", json={"name": "x"})
    assert r.status_code == 404


def test_delete_idempotent(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    r = client.delete(f"/v1/projects/{pid}/labelsets/nope")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_delete_existing(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    lsid = client.post(
        f"/v1/projects/{pid}/labelsets",
        json={"name": "L", "type": "bbox"},
    ).json()["labelset"]["id"]
    r = client.delete(f"/v1/projects/{pid}/labelsets/{lsid}")
    assert r.status_code == 200
    assert client.get(f"/v1/projects/{pid}/labelsets/{lsid}").status_code == 404


# ----- list summary aggregates -----------------------------------------


def test_list_summary_includes_aggregates(client: TestClient) -> None:
    pid, iid = _setup_with_image(client)
    lsid = client.post(
        f"/v1/projects/{pid}/labelsets",
        json={"name": "L", "type": "classify", "imageIds": [iid]},
    ).json()["labelset"]["id"]
    # Add classes via PATCH so the summary's classStats has a row.
    client.patch(
        f"/v1/projects/{pid}/labelsets/{lsid}",
        json={"classes": [{"id": "c1", "name": "x", "color": "#000"}]},
    )
    # Save one annotation referencing the image.
    client.put(
        f"/v1/projects/{pid}/labelsets/{lsid}/annotations",
        json={
            "annotations": [
                {
                    "id": "a1",
                    "imageId": iid,
                    "classId": "c1",
                    "kind": "classify",
                    "createdAt": 0,
                }
            ]
        },
    )
    listed = client.get(f"/v1/projects/{pid}/labelsets").json()["labelsets"]
    assert len(listed) == 1
    s = listed[0]
    assert s["imageCount"] == 1
    assert s["annotationCount"] == 1
    assert s["labeledImageCount"] == 1
    assert s["classStats"] == [{"classId": "c1", "imageCount": 1}]
    assert s["imageLabels"] == {iid: ["c1"]}


# ----- annotations -----------------------------------------------------


def test_annotations_default_empty(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    lsid = client.post(
        f"/v1/projects/{pid}/labelsets",
        json={"name": "L", "type": "bbox"},
    ).json()["labelset"]["id"]
    r = client.get(f"/v1/projects/{pid}/labelsets/{lsid}/annotations")
    assert r.status_code == 200
    assert r.json() == {"annotations": []}


def test_annotations_put_then_get(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    lsid = client.post(
        f"/v1/projects/{pid}/labelsets",
        json={"name": "L", "type": "bbox"},
    ).json()["labelset"]["id"]
    payload = {
        "annotations": [
            {
                "id": "a1",
                "imageId": "i1",
                "classId": "c1",
                "kind": "rect",
                "shape": {"kind": "rect", "x": 0, "y": 0, "w": 0.5, "h": 0.5},
                "createdAt": 0,
            }
        ]
    }
    r = client.put(
        f"/v1/projects/{pid}/labelsets/{lsid}/annotations",
        json=payload,
    )
    assert r.status_code == 200
    got = client.get(f"/v1/projects/{pid}/labelsets/{lsid}/annotations").json()
    assert got == payload


def test_annotations_put_invalid_body_returns_422(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    lsid = client.post(
        f"/v1/projects/{pid}/labelsets",
        json={"name": "L", "type": "bbox"},
    ).json()["labelset"]["id"]
    # Pydantic 422 because `annotations` is missing.
    r = client.put(
        f"/v1/projects/{pid}/labelsets/{lsid}/annotations",
        json={},
    )
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_input"


def test_annotations_put_full_replace(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    lsid = client.post(
        f"/v1/projects/{pid}/labelsets",
        json={"name": "L", "type": "bbox"},
    ).json()["labelset"]["id"]
    client.put(
        f"/v1/projects/{pid}/labelsets/{lsid}/annotations",
        json={
            "annotations": [
                {
                    "id": "a1",
                    "imageId": "i1",
                    "classId": "c1",
                    "kind": "rect",
                    "shape": {"kind": "rect", "x": 0, "y": 0, "w": 0.5, "h": 0.5},
                    "createdAt": 0,
                }
            ]
        },
    )
    # Replace with empty.
    client.put(
        f"/v1/projects/{pid}/labelsets/{lsid}/annotations",
        json={"annotations": []},
    )
    got = client.get(f"/v1/projects/{pid}/labelsets/{lsid}/annotations").json()
    assert got == {"annotations": []}
