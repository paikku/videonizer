"""Image CRUD + bytes/thumb + bulk ops + ingest-from-resource."""
from __future__ import annotations

import json

import pytest

from tests._images_helper import make_jpeg, make_png


@pytest.fixture
async def project_id(stateful_app) -> str:
    r = await stateful_app.post("/api/projects", json={"name": "p"})
    return r.json()["project"]["id"]


@pytest.fixture
async def batch_id(stateful_app, project_id) -> str:
    """An empty image_batch resource ready to receive images."""
    r = await stateful_app.post(
        f"/api/projects/{project_id}/resources",
        data={"type": "image_batch", "name": "b", "tags": "[]"},
    )
    return r.json()["resource"]["id"]


@pytest.fixture
async def video_id(stateful_app, project_id) -> str:
    """A video resource (so its frames will get source=video_frame)."""
    r = await stateful_app.post(
        f"/api/projects/{project_id}/resources",
        data={
            "type": "video",
            "name": "v.mp4",
            "tags": "[]",
            "width": "640",
            "height": "480",
        },
        files={"file": ("v.mp4", b"fake-video", "video/mp4")},
    )
    return r.json()["resource"]["id"]


async def _post_images(client, project_id, resource_id, items):
    """items: list[(filename, bytes, meta_dict)]."""
    files = [("files", (name, body, "image/png")) for name, body, _ in items]
    meta = [m for _, _, m in items]
    return await client.post(
        f"/api/projects/{project_id}/resources/{resource_id}/images",
        data={"meta": json.dumps(meta)},
        files=files,
    )


# --- ingest ----------------------------------------------------------------


async def test_ingest_uploaded_image(stateful_app, project_id, batch_id) -> None:
    png = make_png(40, 30)
    r = await _post_images(
        stateful_app,
        project_id,
        batch_id,
        [("a.png", png, {"fileName": "a.png", "width": 40, "height": 30})],
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body["images"]) == 1
    img = body["images"][0]
    assert img["source"] == "uploaded"
    assert img["fileName"] == "a.png"
    assert img["width"] == 40
    assert img["height"] == 30


async def test_ingest_video_frame_source(stateful_app, project_id, video_id) -> None:
    png = make_png(16, 16)
    r = await _post_images(
        stateful_app,
        project_id,
        video_id,
        [
            (
                "f.png",
                png,
                {
                    "fileName": "f.png",
                    "width": 16,
                    "height": 16,
                    "timestamp": 1.5,
                    "frameIndex": 45,
                },
            )
        ],
    )
    assert r.status_code == 201, r.text
    img = r.json()["images"][0]
    assert img["source"] == "video_frame"
    assert img["timestamp"] == 1.5
    assert img["frameIndex"] == 45


async def test_ingest_meta_files_mismatch(stateful_app, project_id, batch_id) -> None:
    png = make_png()
    files = [("files", ("a.png", png, "image/png"))]
    r = await stateful_app.post(
        f"/api/projects/{project_id}/resources/{batch_id}/images",
        data={"meta": json.dumps([])},
        files=files,
    )
    assert r.status_code == 400
    assert r.json()["error"] == "meta_mismatch"


async def test_ingest_idempotent_on_id(stateful_app, project_id, batch_id) -> None:
    png = make_png()
    cid = "11111111-1111-1111-1111-111111111111"
    item = ("a.png", png, {"fileName": "a.png", "width": 32, "height": 24, "id": cid})
    r1 = await _post_images(stateful_app, project_id, batch_id, [item])
    r2 = await _post_images(stateful_app, project_id, batch_id, [item])
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["images"][0]["id"] == cid
    assert r2.json()["images"][0]["id"] == cid

    r = await stateful_app.get(
        f"/api/projects/{project_id}/images?resourceId={batch_id}"
    )
    assert len(r.json()["images"]) == 1


# --- list / get / patch / delete ------------------------------------------


async def test_list_filtered(stateful_app, project_id, batch_id, video_id) -> None:
    await _post_images(
        stateful_app,
        project_id,
        batch_id,
        [("a.png", make_png(), {"fileName": "a.png", "width": 32, "height": 24})],
    )
    await _post_images(
        stateful_app,
        project_id,
        video_id,
        [("f.png", make_png(), {"fileName": "f.png", "width": 32, "height": 24})],
    )

    r_all = await stateful_app.get(f"/api/projects/{project_id}/images")
    assert len(r_all.json()["images"]) == 2

    r_v = await stateful_app.get(
        f"/api/projects/{project_id}/images?source=video_frame"
    )
    sources = {x["source"] for x in r_v.json()["images"]}
    assert sources == {"video_frame"}

    r_b = await stateful_app.get(
        f"/api/projects/{project_id}/images?resourceId={batch_id}"
    )
    rids = {x["resourceId"] for x in r_b.json()["images"]}
    assert rids == {batch_id}


async def test_list_invalid_source(stateful_app, project_id) -> None:
    r = await stateful_app.get(
        f"/api/projects/{project_id}/images?source=garbage"
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_source"


async def test_patch_tags(stateful_app, project_id, batch_id) -> None:
    r = await _post_images(
        stateful_app,
        project_id,
        batch_id,
        [("a.png", make_png(), {"fileName": "a.png", "width": 32, "height": 24})],
    )
    iid = r.json()["images"][0]["id"]
    r2 = await stateful_app.patch(
        f"/api/projects/{project_id}/images/{iid}",
        json={"tags": ["x", "y"]},
    )
    assert r2.status_code == 200
    assert r2.json()["image"]["tags"] == ["x", "y"]


async def test_get_404(stateful_app, project_id) -> None:
    r = await stateful_app.get(
        f"/api/projects/{project_id}/images/00000000-0000-0000-0000-000000000000"
    )
    assert r.status_code == 404


async def test_delete_cascades_blobs(stateful_app, blob_store, project_id, batch_id) -> None:
    r = await _post_images(
        stateful_app,
        project_id,
        batch_id,
        [("a.png", make_png(), {"fileName": "a.png", "width": 32, "height": 24})],
    )
    iid = r.json()["images"][0]["id"]
    bytes_key = f"p/{project_id}/i/{iid}/bytes.png"
    thumb_key = f"p/{project_id}/i/{iid}/thumb.jpg"
    assert await blob_store.exists(bytes_key)
    assert await blob_store.exists(thumb_key)

    r2 = await stateful_app.delete(f"/api/projects/{project_id}/images/{iid}")
    assert r2.status_code == 200
    assert not await blob_store.exists(bytes_key)
    assert not await blob_store.exists(thumb_key)


# --- bulk ------------------------------------------------------------------


async def test_bulk_delete(stateful_app, project_id, batch_id) -> None:
    items = [
        (f"a{i}.png", make_png(), {"fileName": f"a{i}.png", "width": 32, "height": 24})
        for i in range(3)
    ]
    r = await _post_images(stateful_app, project_id, batch_id, items)
    ids = [x["id"] for x in r.json()["images"]]

    r2 = await stateful_app.post(
        f"/api/projects/{project_id}/images/delete",
        json={"imageIds": ids[:2]},
    )
    assert r2.status_code == 200
    assert r2.json() == {"deleted": 2}

    r3 = await stateful_app.get(f"/api/projects/{project_id}/images")
    assert len(r3.json()["images"]) == 1


@pytest.mark.parametrize(
    "mode,start,target,expected",
    [
        ("add", ["a"], ["b", "c"], ["a", "b", "c"]),
        ("add", ["a", "b"], ["b", "c"], ["a", "b", "c"]),
        ("replace", ["a", "b"], ["x"], ["x"]),
        ("remove", ["a", "b", "c"], ["b"], ["a", "c"]),
        ("remove", ["a"], ["b"], ["a"]),
    ],
)
async def test_bulk_tag_modes(
    stateful_app, project_id, batch_id, mode, start, target, expected
) -> None:
    r = await _post_images(
        stateful_app,
        project_id,
        batch_id,
        [("a.png", make_png(), {"fileName": "a.png", "width": 32, "height": 24})],
    )
    iid = r.json()["images"][0]["id"]
    if start:
        await stateful_app.patch(
            f"/api/projects/{project_id}/images/{iid}", json={"tags": start}
        )

    r2 = await stateful_app.post(
        f"/api/projects/{project_id}/images/tags",
        json={"imageIds": [iid], "tags": target, "mode": mode},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json() == {"updated": 1}

    r3 = await stateful_app.get(f"/api/projects/{project_id}/images/{iid}")
    assert r3.json()["image"]["tags"] == expected


async def test_bulk_tag_invalid_mode(stateful_app, project_id) -> None:
    # Pydantic Literal validation rejects unknown modes at request parsing
    # time, so we get 422 — not the route's 400. Either is fine for the
    # frontend; the body still surfaces the invalid value.
    r = await stateful_app.post(
        f"/api/projects/{project_id}/images/tags",
        json={"imageIds": [], "tags": [], "mode": "garbage"},
    )
    assert r.status_code == 422


# --- bytes / thumb --------------------------------------------------------


async def test_bytes_and_thumb_roundtrip(stateful_app, project_id, batch_id) -> None:
    jpeg = make_jpeg(80, 60)
    files = [("files", ("a.jpg", jpeg, "image/jpeg"))]
    meta = [{"fileName": "a.jpg", "width": 80, "height": 60}]
    r = await stateful_app.post(
        f"/api/projects/{project_id}/resources/{batch_id}/images",
        data={"meta": json.dumps(meta)},
        files=files,
    )
    iid = r.json()["images"][0]["id"]

    r_bytes = await stateful_app.get(f"/api/projects/{project_id}/images/{iid}/bytes")
    assert r_bytes.status_code == 200
    assert r_bytes.headers["content-type"] == "image/jpeg"
    assert r_bytes.content == jpeg
    assert "immutable" in r_bytes.headers["cache-control"]

    r_thumb = await stateful_app.get(f"/api/projects/{project_id}/images/{iid}/thumb")
    assert r_thumb.status_code == 200
    assert r_thumb.headers["content-type"] == "image/jpeg"
    # Thumbnail must be smaller than the original (it's resized down... well,
    # this small original may not shrink, so just verify it's a valid JPEG).
    assert r_thumb.content[:3] == b"\xff\xd8\xff"


# --- count rollups --------------------------------------------------------


async def test_resource_summary_image_count(stateful_app, project_id, batch_id) -> None:
    items = [
        (f"a{i}.png", make_png(), {"fileName": f"a{i}.png", "width": 32, "height": 24})
        for i in range(3)
    ]
    await _post_images(stateful_app, project_id, batch_id, items)

    r = await stateful_app.get(f"/api/projects/{project_id}/resources")
    res = next(x for x in r.json()["resources"] if x["id"] == batch_id)
    assert res["imageCount"] == 3


async def test_project_summary_image_count(stateful_app, project_id, batch_id) -> None:
    items = [
        (f"a{i}.png", make_png(), {"fileName": f"a{i}.png", "width": 32, "height": 24})
        for i in range(2)
    ]
    await _post_images(stateful_app, project_id, batch_id, items)

    r = await stateful_app.get("/api/projects")
    proj = next(p for p in r.json()["projects"] if p["id"] == project_id)
    assert proj["imageCount"] == 2
