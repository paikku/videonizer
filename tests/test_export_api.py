"""Export: JSON download, validate, dataset ZIP stream."""
from __future__ import annotations

import io
import json
import uuid
import zipfile

import pytest

from tests._images_helper import make_jpeg


def _ann_id() -> str:
    return str(uuid.uuid4())


def _rect(image_id: str, class_id: str) -> dict:
    return {
        "id": _ann_id(),
        "imageId": image_id,
        "classId": class_id,
        "kind": "rect",
        "rect": {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5},
    }


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
async def loaded_labelset(stateful_app, project_id, batch_id) -> dict:
    """A labelset with 4 images and a few annotations on the first 2.
    Returns dict with labelset_id, image_ids.
    """
    image_ids: list[str] = []
    for i in range(4):
        files = [("files", (f"img{i}.jpg", make_jpeg(40, 30, color=(i*40, 50, 50)), "image/jpeg"))]
        meta = [{"fileName": f"img{i}.jpg", "width": 40, "height": 30}]
        r = await stateful_app.post(
            f"/api/projects/{project_id}/resources/{batch_id}/images",
            data={"meta": json.dumps(meta)},
            files=files,
        )
        image_ids.append(r.json()["images"][0]["id"])

    r = await stateful_app.post(
        f"/api/projects/{project_id}/labelsets",
        json={"name": "ls", "type": "bbox", "imageIds": image_ids},
    )
    lsid = r.json()["labelset"]["id"]

    await stateful_app.patch(
        f"/api/projects/{project_id}/labelsets/{lsid}",
        json={"classes": [{"id": "c1", "name": "cat"}, {"id": "c2", "name": "dog"}]},
    )
    await stateful_app.put(
        f"/api/projects/{project_id}/labelsets/{lsid}/annotations",
        json={
            "labelSetId": lsid,
            "annotations": [_rect(image_ids[0], "c1"), _rect(image_ids[1], "c2")],
        },
    )
    return {"labelset_id": lsid, "image_ids": image_ids}


# --- validate -------------------------------------------------------------


async def test_validate_default_split(stateful_app, project_id, loaded_labelset) -> None:
    lsid = loaded_labelset["labelset_id"]
    r = await stateful_app.post(
        f"/api/projects/{project_id}/labelsets/{lsid}/export/validate",
        json={},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    rep = body["report"]
    assert rep["totalImages"] == 4
    assert rep["labeledImages"] == 2
    assert rep["unlabeledImages"] == 2
    assert sum(rep["splitCounts"].values()) == 4
    assert len(body["items"]) == 4


async def test_validate_warns_unlabeled_in_train(
    stateful_app, project_id, loaded_labelset
) -> None:
    lsid = loaded_labelset["labelset_id"]
    # Force everything into train so unlabeled images definitely land there.
    r = await stateful_app.post(
        f"/api/projects/{project_id}/labelsets/{lsid}/export/validate",
        json={"split": {"train": 1.0, "val": 0.0, "test": 0.0}},
    )
    body = r.json()
    assert body["report"]["splitCounts"]["train"] == 4
    assert any("unlabeled" in w for w in body["report"]["warnings"])


# --- JSON download --------------------------------------------------------


async def test_export_json_download(stateful_app, project_id, loaded_labelset) -> None:
    lsid = loaded_labelset["labelset_id"]
    r = await stateful_app.get(
        f"/api/projects/{project_id}/labelsets/{lsid}/export"
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert "attachment" in r.headers["content-disposition"]
    body = json.loads(r.content)
    assert body["labelSetId"] == lsid
    assert body["type"] == "bbox"
    assert len(body["annotations"]) == 2
    assert len(body["items"]) == 4


# --- dataset ZIP ----------------------------------------------------------


async def test_export_dataset_zip(stateful_app, project_id, loaded_labelset) -> None:
    lsid = loaded_labelset["labelset_id"]
    r = await stateful_app.post(
        f"/api/projects/{project_id}/labelsets/{lsid}/export/dataset",
        json={"includeImages": True},
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"]

    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert "annotations.json" in names
    image_names = [n for n in names if n.startswith("images/")]
    # 4 images, none excluded
    assert len(image_names) == 4

    manifest = json.loads(zf.read("annotations.json"))
    assert manifest["labelSetId"] == lsid
    assert len(manifest["annotations"]) == 2
    assert manifest["classes"][0]["id"] == "c1"


async def test_export_dataset_without_images(stateful_app, project_id, loaded_labelset) -> None:
    lsid = loaded_labelset["labelset_id"]
    r = await stateful_app.post(
        f"/api/projects/{project_id}/labelsets/{lsid}/export/dataset",
        json={"includeImages": False},
    )
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert names == ["annotations.json"]


async def test_export_remap_class_ids(stateful_app, project_id, loaded_labelset) -> None:
    lsid = loaded_labelset["labelset_id"]
    r = await stateful_app.post(
        f"/api/projects/{project_id}/labelsets/{lsid}/export/dataset",
        json={"includeImages": False, "remapClassIds": True},
    )
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    manifest = json.loads(zf.read("annotations.json"))
    class_ids = {a["classId"] for a in manifest["annotations"]}
    # Class ids should be remapped to integer indices 0..N-1.
    assert class_ids.issubset({0, 1})


async def test_validate_404_on_missing_labelset(stateful_app, project_id) -> None:
    r = await stateful_app.post(
        f"/api/projects/{project_id}/labelsets/00000000-0000-0000-0000-000000000000/export/validate",
        json={},
    )
    assert r.status_code == 404
