"""Resource CRUD + upload + range + previews + bulk delete."""
from __future__ import annotations

import json

import pytest


@pytest.fixture
async def project_id(stateful_app) -> str:
    r = await stateful_app.post("/api/projects", json={"name": "p"})
    return r.json()["project"]["id"]


# --- list / create ---------------------------------------------------------


async def test_list_empty_for_project(stateful_app, project_id) -> None:
    r = await stateful_app.get(f"/api/projects/{project_id}/resources")
    assert r.status_code == 200
    assert r.json() == {"resources": []}


async def test_list_404_when_project_missing(stateful_app) -> None:
    r = await stateful_app.get(
        "/api/projects/00000000-0000-0000-0000-000000000000/resources"
    )
    assert r.status_code == 404
    assert r.json()["error"] == "project_not_found"


async def test_create_image_batch(stateful_app, project_id) -> None:
    r = await stateful_app.post(
        f"/api/projects/{project_id}/resources",
        data={
            "type": "image_batch",
            "name": "batch one",
            "tags": json.dumps(["wild", "raw"]),
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()["resource"]
    assert body["type"] == "image_batch"
    assert body["name"] == "batch one"
    assert body["tags"] == ["wild", "raw"]
    assert body["hasSource"] is False
    assert body["previewCount"] == 0


async def test_create_video_uploads_source(stateful_app, project_id) -> None:
    payload = b"FAKEMP4DATA" * 100  # 1100 bytes
    r = await stateful_app.post(
        f"/api/projects/{project_id}/resources",
        data={
            "type": "video",
            "name": "clip.mp4",
            "tags": "[]",
            "width": "640",
            "height": "480",
            "duration": "12.5",
        },
        files={"file": ("clip.mp4", payload, "video/mp4")},
    )
    assert r.status_code == 201, r.text
    body = r.json()["resource"]
    assert body["type"] == "video"
    assert body["width"] == 640
    assert body["height"] == 480
    assert body["duration"] == 12.5
    assert body["hasSource"] is True


async def test_create_video_requires_dimensions(stateful_app, project_id) -> None:
    r = await stateful_app.post(
        f"/api/projects/{project_id}/resources",
        data={"type": "video", "name": "clip.mp4", "tags": "[]"},
        files={"file": ("clip.mp4", b"x", "video/mp4")},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "missing_dimensions"


async def test_create_video_requires_file(stateful_app, project_id) -> None:
    r = await stateful_app.post(
        f"/api/projects/{project_id}/resources",
        data={
            "type": "video",
            "name": "clip.mp4",
            "tags": "[]",
            "width": "640",
            "height": "480",
        },
    )
    assert r.status_code == 400
    assert r.json()["error"] == "missing_file"


async def test_create_invalid_type(stateful_app, project_id) -> None:
    r = await stateful_app.post(
        f"/api/projects/{project_id}/resources",
        data={"type": "garbage", "name": "n", "tags": "[]"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_type"


async def test_create_invalid_tags_json(stateful_app, project_id) -> None:
    r = await stateful_app.post(
        f"/api/projects/{project_id}/resources",
        data={"type": "image_batch", "name": "n", "tags": "not-json"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_tags"


# --- get / patch / delete -------------------------------------------------


async def test_get_returns_resource(stateful_app, project_id) -> None:
    r = await stateful_app.post(
        f"/api/projects/{project_id}/resources",
        data={"type": "image_batch", "name": "n", "tags": "[]"},
    )
    rid = r.json()["resource"]["id"]
    r2 = await stateful_app.get(f"/api/projects/{project_id}/resources/{rid}")
    assert r2.status_code == 200
    assert r2.json()["resource"]["id"] == rid


async def test_patch_name_and_tags(stateful_app, project_id) -> None:
    r = await stateful_app.post(
        f"/api/projects/{project_id}/resources",
        data={"type": "image_batch", "name": "old", "tags": "[]"},
    )
    rid = r.json()["resource"]["id"]
    r2 = await stateful_app.patch(
        f"/api/projects/{project_id}/resources/{rid}",
        json={"name": "new", "tags": ["a", "b"]},
    )
    assert r2.status_code == 200
    body = r2.json()["resource"]
    assert body["name"] == "new"
    assert body["tags"] == ["a", "b"]


async def test_delete_cascades_blobs(stateful_app, blob_store, project_id) -> None:
    payload = b"BYTES" * 200
    r = await stateful_app.post(
        f"/api/projects/{project_id}/resources",
        data={
            "type": "video",
            "name": "v",
            "tags": "[]",
            "width": "16",
            "height": "9",
        },
        files={"file": ("v.mp4", payload, "video/mp4")},
    )
    rid = r.json()["resource"]["id"]

    # Sanity: source is in S3.
    source_key = f"p/{project_id}/r/{rid}/source.mp4"
    assert await blob_store.exists(source_key)

    r2 = await stateful_app.delete(f"/api/projects/{project_id}/resources/{rid}")
    assert r2.status_code == 200

    assert not await blob_store.exists(source_key)
    r3 = await stateful_app.get(f"/api/projects/{project_id}/resources/{rid}")
    assert r3.status_code == 404


async def test_delete_404_when_missing(stateful_app, project_id) -> None:
    r = await stateful_app.delete(
        f"/api/projects/{project_id}/resources/00000000-0000-0000-0000-000000000000"
    )
    assert r.status_code == 404
    assert r.json()["error"] == "resource_not_found"


# --- bulk delete -----------------------------------------------------------


async def test_bulk_delete(stateful_app, project_id) -> None:
    ids = []
    for i in range(3):
        r = await stateful_app.post(
            f"/api/projects/{project_id}/resources",
            data={"type": "image_batch", "name": f"b{i}", "tags": "[]"},
        )
        ids.append(r.json()["resource"]["id"])

    r = await stateful_app.post(
        f"/api/projects/{project_id}/resources/delete",
        json={"resourceIds": ids[:2]},
    )
    assert r.status_code == 200
    assert r.json() == {"deleted": 2}

    r = await stateful_app.get(f"/api/projects/{project_id}/resources")
    remaining = [x["id"] for x in r.json()["resources"]]
    assert remaining == [ids[2]]


async def test_bulk_delete_empty(stateful_app, project_id) -> None:
    r = await stateful_app.post(
        f"/api/projects/{project_id}/resources/delete",
        json={"resourceIds": []},
    )
    assert r.status_code == 200
    assert r.json() == {"deleted": 0}


# --- source serving (Range) ------------------------------------------------


async def _create_video(client, project_id: str, payload: bytes) -> str:
    r = await client.post(
        f"/api/projects/{project_id}/resources",
        data={
            "type": "video",
            "name": "v.mp4",
            "tags": "[]",
            "width": "16",
            "height": "9",
        },
        files={"file": ("v.mp4", payload, "video/mp4")},
    )
    assert r.status_code == 201, r.text
    return r.json()["resource"]["id"]


async def test_get_source_full(stateful_app, project_id) -> None:
    payload = bytes(range(256)) * 4
    rid = await _create_video(stateful_app, project_id, payload)

    r = await stateful_app.get(f"/api/projects/{project_id}/resources/{rid}/source")
    assert r.status_code == 200
    assert r.headers["accept-ranges"] == "bytes"
    assert r.content == payload


async def test_get_source_range(stateful_app, project_id) -> None:
    payload = bytes(range(256)) * 4  # 1024 bytes
    rid = await _create_video(stateful_app, project_id, payload)

    r = await stateful_app.get(
        f"/api/projects/{project_id}/resources/{rid}/source",
        headers={"Range": "bytes=100-199"},
    )
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 100-199/{len(payload)}"
    assert r.headers["content-length"] == "100"
    assert r.content == payload[100:200]


async def test_get_source_range_open_ended(stateful_app, project_id) -> None:
    payload = b"0123456789" * 10  # 100 bytes
    rid = await _create_video(stateful_app, project_id, payload)

    r = await stateful_app.get(
        f"/api/projects/{project_id}/resources/{rid}/source",
        headers={"Range": "bytes=90-"},
    )
    assert r.status_code == 206
    assert r.content == payload[90:]


async def test_get_source_range_unsatisfiable(stateful_app, project_id) -> None:
    payload = b"x" * 50
    rid = await _create_video(stateful_app, project_id, payload)

    r = await stateful_app.get(
        f"/api/projects/{project_id}/resources/{rid}/source",
        headers={"Range": "bytes=500-600"},
    )
    assert r.status_code == 416
    assert r.headers["content-range"] == "bytes */50"


async def test_get_source_404_for_image_batch(stateful_app, project_id) -> None:
    r = await stateful_app.post(
        f"/api/projects/{project_id}/resources",
        data={"type": "image_batch", "name": "b", "tags": "[]"},
    )
    rid = r.json()["resource"]["id"]
    r2 = await stateful_app.get(f"/api/projects/{project_id}/resources/{rid}/source")
    assert r2.status_code == 404
    assert r2.json()["error"] == "source_not_available"


# --- previews --------------------------------------------------------------


async def test_upload_and_get_previews(stateful_app, project_id) -> None:
    rid = await _create_video(stateful_app, project_id, b"ignored")

    files = [
        ("files", (f"p{i}.jpg", bytes([i]) * 32, "image/jpeg")) for i in range(3)
    ]
    r = await stateful_app.post(
        f"/api/projects/{project_id}/resources/{rid}/previews",
        files=files,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"previewCount": 3}

    # GET each tile
    for i in range(3):
        r2 = await stateful_app.get(
            f"/api/projects/{project_id}/resources/{rid}/previews/{i}"
        )
        assert r2.status_code == 200
        assert r2.headers["content-type"] == "image/jpeg"
        assert r2.content == bytes([i]) * 32

    # Resource view reflects the count.
    r3 = await stateful_app.get(f"/api/projects/{project_id}/resources/{rid}")
    assert r3.json()["resource"]["previewCount"] == 3


async def test_preview_idx_out_of_range(stateful_app, project_id) -> None:
    rid = await _create_video(stateful_app, project_id, b"x")
    files = [("files", ("p.jpg", b"x", "image/jpeg"))]
    await stateful_app.post(
        f"/api/projects/{project_id}/resources/{rid}/previews", files=files
    )
    r = await stateful_app.get(
        f"/api/projects/{project_id}/resources/{rid}/previews/99"
    )
    assert r.status_code == 404


# --- project summary now reflects resource count --------------------------


async def test_project_summary_resource_count(stateful_app, project_id) -> None:
    for i in range(2):
        await stateful_app.post(
            f"/api/projects/{project_id}/resources",
            data={"type": "image_batch", "name": f"b{i}", "tags": "[]"},
        )

    r = await stateful_app.get("/api/projects")
    proj = next(p for p in r.json()["projects"] if p["id"] == project_id)
    assert proj["resourceCount"] == 2


# Image ingestion (POST /resources/{rid}/images) is fully tested in
# test_images_api.py; no stub-shaped test here anymore.
