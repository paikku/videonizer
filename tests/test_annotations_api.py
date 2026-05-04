"""Annotation GET / PUT / PATCH semantics."""
from __future__ import annotations

import json
import uuid

import pytest

from tests._images_helper import make_png


@pytest.fixture
async def project_id(stateful_app) -> str:
    r = await stateful_app.post("/api/projects", json={"name": "p"})
    return r.json()["project"]["id"]


@pytest.fixture
async def batch_id(stateful_app, project_id) -> str:
    r = await stateful_app.post(
        f"/api/projects/{project_id}/resources",
        data={"type": "image_batch", "name": "b", "tags": "[]"},
    )
    return r.json()["resource"]["id"]


@pytest.fixture
async def two_images(stateful_app, project_id, batch_id) -> tuple[str, str]:
    files = [
        ("files", ("a.png", make_png(), "image/png")),
        ("files", ("b.png", make_png(), "image/png")),
    ]
    meta = [
        {"fileName": "a.png", "width": 32, "height": 24},
        {"fileName": "b.png", "width": 32, "height": 24},
    ]
    r = await stateful_app.post(
        f"/api/projects/{project_id}/resources/{batch_id}/images",
        data={"meta": json.dumps(meta)},
        files=files,
    )
    ids = [x["id"] for x in r.json()["images"]]
    return ids[0], ids[1]


@pytest.fixture
async def labelset_id(stateful_app, project_id, two_images) -> str:
    a, b = two_images
    r = await stateful_app.post(
        f"/api/projects/{project_id}/labelsets",
        json={"name": "ls", "type": "polygon", "imageIds": [a, b]},
    )
    return r.json()["labelset"]["id"]


def _ann_id() -> str:
    return str(uuid.uuid4())


def _rect(image_id: str, class_id: str, x=0.1, y=0.1, w=0.5, h=0.5) -> dict:
    return {
        "id": _ann_id(),
        "imageId": image_id,
        "classId": class_id,
        "kind": "rect",
        "rect": {"x": x, "y": y, "w": w, "h": h},
    }


def _polygon(image_id: str, class_id: str) -> dict:
    return {
        "id": _ann_id(),
        "imageId": image_id,
        "classId": class_id,
        "kind": "polygon",
        "polygon": [[[0.1, 0.1], [0.9, 0.1], [0.5, 0.9]]],
    }


# --- GET / PUT ------------------------------------------------------------


async def test_get_empty(stateful_app, project_id, labelset_id) -> None:
    r = await stateful_app.get(
        f"/api/projects/{project_id}/labelsets/{labelset_id}/annotations"
    )
    assert r.status_code == 200
    assert r.json()["annotations"] == []


async def test_put_replaces_all(stateful_app, project_id, labelset_id, two_images) -> None:
    a, _ = two_images
    payload = {
        "labelSetId": labelset_id,
        "annotations": [_rect(a, "c1"), _polygon(a, "c2")],
    }
    r = await stateful_app.put(
        f"/api/projects/{project_id}/labelsets/{labelset_id}/annotations",
        json=payload,
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    r2 = await stateful_app.get(
        f"/api/projects/{project_id}/labelsets/{labelset_id}/annotations"
    )
    body = r2.json()
    assert len(body["annotations"]) == 2
    kinds = sorted(x["kind"] for x in body["annotations"])
    assert kinds == ["polygon", "rect"]


async def test_put_then_put_replaces_completely(
    stateful_app, project_id, labelset_id, two_images
) -> None:
    a, b = two_images
    await stateful_app.put(
        f"/api/projects/{project_id}/labelsets/{labelset_id}/annotations",
        json={"labelSetId": labelset_id, "annotations": [_rect(a, "c1")]},
    )
    await stateful_app.put(
        f"/api/projects/{project_id}/labelsets/{labelset_id}/annotations",
        json={"labelSetId": labelset_id, "annotations": [_rect(b, "c2")]},
    )
    r = await stateful_app.get(
        f"/api/projects/{project_id}/labelsets/{labelset_id}/annotations"
    )
    anns = r.json()["annotations"]
    assert len(anns) == 1
    assert anns[0]["imageId"] == b


# --- PATCH ----------------------------------------------------------------


async def test_patch_upsert_inserts_then_overwrites(
    stateful_app, project_id, labelset_id, two_images
) -> None:
    a, _ = two_images
    rect_id = _ann_id()
    rect = {
        "id": rect_id,
        "imageId": a,
        "classId": "c1",
        "kind": "rect",
        "rect": {"x": 0, "y": 0, "w": 0.4, "h": 0.4},
    }

    r1 = await stateful_app.patch(
        f"/api/projects/{project_id}/labelsets/{labelset_id}/annotations",
        json={"upsert": [rect]},
    )
    assert r1.status_code == 200
    assert len(r1.json()["annotations"]) == 1

    rect_updated = {**rect, "rect": {"x": 0, "y": 0, "w": 0.9, "h": 0.9}}
    r2 = await stateful_app.patch(
        f"/api/projects/{project_id}/labelsets/{labelset_id}/annotations",
        json={"upsert": [rect_updated]},
    )
    body = r2.json()
    assert len(body["annotations"]) == 1
    assert body["annotations"][0]["rect"]["w"] == 0.9


async def test_patch_delete_ids(
    stateful_app, project_id, labelset_id, two_images
) -> None:
    a, _ = two_images
    r1 = _rect(a, "c1")
    r2 = _rect(a, "c1")
    await stateful_app.put(
        f"/api/projects/{project_id}/labelsets/{labelset_id}/annotations",
        json={"labelSetId": labelset_id, "annotations": [r1, r2]},
    )

    rp = await stateful_app.patch(
        f"/api/projects/{project_id}/labelsets/{labelset_id}/annotations",
        json={"deleteIds": [r1["id"]]},
    )
    body = rp.json()
    assert len(body["annotations"]) == 1
    assert body["annotations"][0]["id"] == r2["id"]


async def test_patch_replace_image_ids_then_upsert(
    stateful_app, project_id, labelset_id, two_images
) -> None:
    a, b = two_images
    # Seed: 2 annotations on a, 1 on b
    seed = [_rect(a, "c1"), _rect(a, "c2"), _rect(b, "c1")]
    await stateful_app.put(
        f"/api/projects/{project_id}/labelsets/{labelset_id}/annotations",
        json={"labelSetId": labelset_id, "annotations": seed},
    )

    # PATCH: blow away annotations on a, then add a single new one on a
    new = _rect(a, "redo")
    rp = await stateful_app.patch(
        f"/api/projects/{project_id}/labelsets/{labelset_id}/annotations",
        json={"replaceImageIds": [a], "upsert": [new]},
    )
    body = rp.json()
    by_image: dict[str, list] = {}
    for ann in body["annotations"]:
        by_image.setdefault(ann["imageId"], []).append(ann)
    assert len(by_image[a]) == 1
    assert by_image[a][0]["classId"] == "redo"
    assert len(by_image[b]) == 1


async def test_patch_order_replace_then_delete_then_upsert(
    stateful_app, project_id, labelset_id, two_images
) -> None:
    """Single PATCH must apply replaceImageIds → deleteIds → upsert. The
    upsert must survive even when its id is in deleteIds *and* its
    imageId is in replaceImageIds — because upsert runs last and inserts
    cleanly after both deletes.
    """
    a, b = two_images
    initial = [_rect(a, "c1"), _rect(b, "c2")]
    await stateful_app.put(
        f"/api/projects/{project_id}/labelsets/{labelset_id}/annotations",
        json={"labelSetId": labelset_id, "annotations": initial},
    )

    new_id = _ann_id()
    new = {
        "id": new_id,
        "imageId": a,
        "classId": "fresh",
        "kind": "rect",
        "rect": {"x": 0, "y": 0, "w": 1, "h": 1},
    }
    rp = await stateful_app.patch(
        f"/api/projects/{project_id}/labelsets/{labelset_id}/annotations",
        json={
            "replaceImageIds": [a],
            "deleteIds": [new_id],  # no-op (doesn't exist yet)
            "upsert": [new],
        },
    )
    anns = rp.json()["annotations"]
    ids_on_a = [x["id"] for x in anns if x["imageId"] == a]
    assert ids_on_a == [new_id]


# --- 404s -----------------------------------------------------------------


async def test_get_404_on_missing_labelset(stateful_app, project_id) -> None:
    r = await stateful_app.get(
        f"/api/projects/{project_id}/labelsets/00000000-0000-0000-0000-000000000000/annotations"
    )
    assert r.status_code == 404


# --- summary integration --------------------------------------------------


async def test_summary_reflects_annotations(
    stateful_app, project_id, labelset_id, two_images
) -> None:
    a, b = two_images
    await stateful_app.put(
        f"/api/projects/{project_id}/labelsets/{labelset_id}/annotations",
        json={
            "labelSetId": labelset_id,
            "annotations": [_rect(a, "c1"), _rect(a, "c1"), _polygon(b, "c2")],
        },
    )
    r = await stateful_app.get(
        f"/api/projects/{project_id}/labelsets/{labelset_id}/summary"
    )
    s = r.json()["summary"]
    assert s["annotationCount"] == 3
    assert s["labeledImageCount"] == 2
    # imageLabels: a→{c1}, b→{c2}
    assert sorted(s["imageLabels"][a]) == ["c1"]
    assert sorted(s["imageLabels"][b]) == ["c2"]
    # imageShapes: a has 2, b has 1
    assert len(s["imageShapes"][a]) == 2
    assert len(s["imageShapes"][b]) == 1
    # classStats
    by_class = {x["classId"]: x for x in s["classStats"]}
    assert by_class["c1"]["annotationCount"] == 2
    assert by_class["c1"]["imageCount"] == 1
    assert by_class["c2"]["annotationCount"] == 1
