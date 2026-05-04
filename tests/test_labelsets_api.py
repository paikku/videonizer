"""LabelSet CRUD with list/summary split."""
from __future__ import annotations

import pytest


@pytest.fixture
async def project_id(stateful_app) -> str:
    r = await stateful_app.post("/api/projects", json={"name": "p"})
    return r.json()["project"]["id"]


# --- create / get / list --------------------------------------------------


async def test_create_minimal(stateful_app, project_id) -> None:
    r = await stateful_app.post(
        f"/api/projects/{project_id}/labelsets",
        json={"name": "ls1", "type": "bbox"},
    )
    assert r.status_code == 201, r.text
    body = r.json()["labelset"]
    assert body["name"] == "ls1"
    assert body["type"] == "bbox"
    assert body["imageIds"] == []
    assert body["classes"] == []


async def test_create_invalid_type(stateful_app, project_id) -> None:
    r = await stateful_app.post(
        f"/api/projects/{project_id}/labelsets",
        json={"name": "n", "type": "garbage"},
    )
    assert r.status_code == 422


async def test_create_in_unknown_project_404(stateful_app) -> None:
    r = await stateful_app.post(
        "/api/projects/00000000-0000-0000-0000-000000000000/labelsets",
        json={"name": "n", "type": "bbox"},
    )
    assert r.status_code == 404


async def test_get_returns_full(stateful_app, project_id) -> None:
    r = await stateful_app.post(
        f"/api/projects/{project_id}/labelsets",
        json={"name": "ls", "type": "polygon", "imageIds": ["a", "b"]},
    )
    lsid = r.json()["labelset"]["id"]
    r2 = await stateful_app.get(f"/api/projects/{project_id}/labelsets/{lsid}")
    assert r2.status_code == 200
    body = r2.json()["labelset"]
    assert body["imageIds"] == ["a", "b"]


async def test_get_404(stateful_app, project_id) -> None:
    r = await stateful_app.get(
        f"/api/projects/{project_id}/labelsets/00000000-0000-0000-0000-000000000000"
    )
    assert r.status_code == 404


async def test_list_lightweight_does_not_include_image_shapes(
    stateful_app, project_id
) -> None:
    await stateful_app.post(
        f"/api/projects/{project_id}/labelsets",
        json={"name": "ls", "type": "bbox", "imageIds": ["a", "b", "c"]},
    )
    r = await stateful_app.get(f"/api/projects/{project_id}/labelsets")
    items = r.json()["labelsets"]
    assert len(items) == 1
    item = items[0]
    # Lightweight contract: no imageShapes, no imageLabels.
    assert "imageShapes" not in item
    assert "imageLabels" not in item
    assert item["imageCount"] == 3
    assert item["excludedImageCount"] == 0


# --- summary --------------------------------------------------------------


async def test_summary_includes_heavy_fields(stateful_app, project_id) -> None:
    r = await stateful_app.post(
        f"/api/projects/{project_id}/labelsets",
        json={"name": "ls", "type": "classify", "imageIds": ["a", "b"]},
    )
    lsid = r.json()["labelset"]["id"]

    r2 = await stateful_app.get(
        f"/api/projects/{project_id}/labelsets/{lsid}/summary"
    )
    assert r2.status_code == 200
    assert r2.headers["cache-control"] == "no-store"
    summary = r2.json()["summary"]
    # Heavy fields exist (empty until PR #6).
    assert summary["imageLabels"] == {}
    assert summary["imageShapes"] == {}
    # Counts still inherited from list item shape.
    assert summary["imageCount"] == 2
    # Annotation-derived counts are 0 until PR #6.
    assert summary["annotationCount"] == 0
    assert summary["labeledImageCount"] == 0


# --- patch / delete -------------------------------------------------------


async def test_patch_classes_and_imageids(stateful_app, project_id) -> None:
    r = await stateful_app.post(
        f"/api/projects/{project_id}/labelsets",
        json={"name": "ls", "type": "bbox"},
    )
    lsid = r.json()["labelset"]["id"]

    r2 = await stateful_app.patch(
        f"/api/projects/{project_id}/labelsets/{lsid}",
        json={
            "classes": [{"id": "c1", "name": "cat"}, {"id": "c2", "name": "dog"}],
            "imageIds": ["x", "y"],
            "excludedImageIds": ["z"],
            "description": "hello",
        },
    )
    assert r2.status_code == 200
    body = r2.json()["labelset"]
    assert body["description"] == "hello"
    assert {c["id"] for c in body["classes"]} == {"c1", "c2"}
    assert body["imageIds"] == ["x", "y"]
    assert body["excludedImageIds"] == ["z"]


async def test_delete(stateful_app, project_id) -> None:
    r = await stateful_app.post(
        f"/api/projects/{project_id}/labelsets",
        json={"name": "ls", "type": "bbox"},
    )
    lsid = r.json()["labelset"]["id"]

    r2 = await stateful_app.delete(
        f"/api/projects/{project_id}/labelsets/{lsid}"
    )
    assert r2.status_code == 200
    r3 = await stateful_app.get(
        f"/api/projects/{project_id}/labelsets/{lsid}"
    )
    assert r3.status_code == 404


async def test_delete_404(stateful_app, project_id) -> None:
    r = await stateful_app.delete(
        f"/api/projects/{project_id}/labelsets/00000000-0000-0000-0000-000000000000"
    )
    assert r.status_code == 404


# --- project summary now reflects label set count -------------------------


async def test_project_summary_labelset_count(stateful_app, project_id) -> None:
    for n in ["a", "b"]:
        await stateful_app.post(
            f"/api/projects/{project_id}/labelsets",
            json={"name": n, "type": "bbox"},
        )
    r = await stateful_app.get("/api/projects")
    proj = next(p for p in r.json()["projects"] if p["id"] == project_id)
    assert proj["labelSetCount"] == 2
