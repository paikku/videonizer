"""Integration tests for the LabelSet export routes."""
from __future__ import annotations

import io
import json
import zipfile
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
    img = PILImage.new("RGB", (4, 4), (10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _setup_labelset(client: TestClient) -> tuple[str, str, str]:
    """Create a project + image_batch + image + bbox-LabelSet wired together.
    Returns ``(project_id, labelset_id, image_id)``."""
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    rid = client.post(
        f"/v1/projects/{pid}/resources",
        data={"type": "image_batch", "name": "A"},
    ).json()["resource"]["id"]
    iid = client.post(
        f"/v1/projects/{pid}/resources/{rid}/images",
        data={"meta": json.dumps([{"fileName": "photo.png", "width": 4, "height": 4}])},
        files=[("files", ("photo.png", _png(), "image/png"))],
    ).json()["images"][0]["id"]
    lsid = client.post(
        f"/v1/projects/{pid}/labelsets",
        json={"name": "MyLabel", "type": "bbox", "imageIds": [iid]},
    ).json()["labelset"]["id"]
    client.patch(
        f"/v1/projects/{pid}/labelsets/{lsid}",
        json={"classes": [{"id": "c1", "name": "thing", "color": "#000"}]},
    )
    client.put(
        f"/v1/projects/{pid}/labelsets/{lsid}/annotations",
        json={
            "annotations": [
                {
                    "id": "a1",
                    "imageId": iid,
                    "classId": "c1",
                    "kind": "rect",
                    "shape": {"kind": "rect", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2},
                    "createdAt": 0,
                }
            ]
        },
    )
    return pid, lsid, iid


# ----- /export (JSON) --------------------------------------------------


def test_export_json_returns_payload_and_disposition(client: TestClient) -> None:
    pid, lsid, iid = _setup_labelset(client)
    r = client.get(f"/v1/projects/{pid}/labelsets/{lsid}/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert "MyLabel.json" in r.headers["content-disposition"]
    body = r.json()
    assert body["version"] == 2
    assert body["labelSet"]["id"] == lsid
    assert [i["id"] for i in body["images"]] == [iid]
    assert body["annotations"][0]["imageId"] == iid


def test_export_json_safe_filename(client: TestClient) -> None:
    """LabelSet name with spaces / unsafe chars is sanitized for the
    Content-Disposition filename."""
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    lsid = client.post(
        f"/v1/projects/{pid}/labelsets",
        json={"name": "My Label / Set!", "type": "bbox"},
    ).json()["labelset"]["id"]
    r = client.get(f"/v1/projects/{pid}/labelsets/{lsid}/export")
    assert r.status_code == 200
    assert r.headers["content-disposition"].endswith('.json"')
    assert "My_Label" in r.headers["content-disposition"]


def test_export_json_missing_returns_404(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    r = client.get(f"/v1/projects/{pid}/labelsets/nope/export")
    assert r.status_code == 404


# ----- /export/dataset (ZIP) ------------------------------------------


def test_dataset_yolo_detection_zip(client: TestClient) -> None:
    pid, lsid, _ = _setup_labelset(client)
    r = client.post(
        f"/v1/projects/{pid}/labelsets/{lsid}/export/dataset",
        json={"split": {"mode": "none"}, "includeImages": False, "remapClassIds": False},
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "MyLabel-yolo-detection.zip" in r.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = set(zf.namelist())
        assert "labels/train/photo.txt" in names
        assert "classes.txt" in names
        assert "data.yaml" in names
        assert "manifest.json" in names


def test_dataset_with_images_includes_bytes(client: TestClient) -> None:
    pid, lsid, _ = _setup_labelset(client)
    r = client.post(
        f"/v1/projects/{pid}/labelsets/{lsid}/export/dataset",
        json={"split": {"mode": "none"}, "includeImages": True, "remapClassIds": False},
    )
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert "images/train/photo.png" in zf.namelist()


def test_dataset_random_split_assigns(client: TestClient) -> None:
    """Build a LabelSet with several images so a random split can spread."""
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    rid = client.post(
        f"/v1/projects/{pid}/resources",
        data={"type": "image_batch", "name": "A"},
    ).json()["resource"]["id"]
    metas, files = [], []
    for i in range(10):
        metas.append({"fileName": f"img{i}.png", "width": 4, "height": 4})
        files.append(("files", (f"img{i}.png", _png(), "image/png")))
    images = client.post(
        f"/v1/projects/{pid}/resources/{rid}/images",
        data={"meta": json.dumps(metas)},
        files=files,
    ).json()["images"]
    lsid = client.post(
        f"/v1/projects/{pid}/labelsets",
        json={"name": "L", "type": "bbox", "imageIds": [i["id"] for i in images]},
    ).json()["labelset"]["id"]
    client.patch(
        f"/v1/projects/{pid}/labelsets/{lsid}",
        json={"classes": [{"id": "c", "name": "x", "color": "#000"}]},
    )
    annotations = [
        {
            "id": f"a{idx}",
            "imageId": img["id"],
            "classId": "c",
            "kind": "rect",
            "shape": {"kind": "rect", "x": 0, "y": 0, "w": 0.5, "h": 0.5},
            "createdAt": 0,
        }
        for idx, img in enumerate(images)
    ]
    client.put(
        f"/v1/projects/{pid}/labelsets/{lsid}/annotations",
        json={"annotations": annotations},
    )
    r = client.post(
        f"/v1/projects/{pid}/labelsets/{lsid}/export/dataset",
        json={
            "split": {"mode": "random", "train": 8, "val": 1, "test": 1, "seed": 1},
            "includeImages": False,
            "remapClassIds": False,
        },
    )
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        splits = {m["imageId"]: m["split"] for m in manifest["images"]}
        assert set(splits.values()).issubset({"train", "val", "test"})
        assert len(splits) == 10


def test_dataset_classify_csv(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    rid = client.post(
        f"/v1/projects/{pid}/resources",
        data={"type": "image_batch", "name": "A"},
    ).json()["resource"]["id"]
    iid = client.post(
        f"/v1/projects/{pid}/resources/{rid}/images",
        data={"meta": json.dumps([{"fileName": "a.png", "width": 4, "height": 4}])},
        files=[("files", ("a.png", _png(), "image/png"))],
    ).json()["images"][0]["id"]
    lsid = client.post(
        f"/v1/projects/{pid}/labelsets",
        json={"name": "L", "type": "classify", "imageIds": [iid]},
    ).json()["labelset"]["id"]
    client.patch(
        f"/v1/projects/{pid}/labelsets/{lsid}",
        json={"classes": [{"id": "c1", "name": "x", "color": "#000"}]},
    )
    client.put(
        f"/v1/projects/{pid}/labelsets/{lsid}/annotations",
        json={"annotations": [{
            "id": "a1", "imageId": iid, "classId": "c1", "kind": "classify", "createdAt": 0,
        }]},
    )
    r = client.post(
        f"/v1/projects/{pid}/labelsets/{lsid}/export/dataset",
        json={"split": {"mode": "none"}, "includeImages": False, "remapClassIds": False},
    )
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert "data.csv" in zf.namelist()
        csv = zf.read("data.csv").decode("utf-8")
        assert "filename,class_name,class_id" in csv


def test_dataset_missing_returns_404(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    r = client.post(
        f"/v1/projects/{pid}/labelsets/nope/export/dataset",
        json={},
    )
    assert r.status_code == 404


# ----- /export/validate ------------------------------------------------


def test_validate_returns_report_and_items(client: TestClient) -> None:
    pid, lsid, iid = _setup_labelset(client)
    r = client.post(
        f"/v1/projects/{pid}/labelsets/{lsid}/export/validate",
        json={"split": {"mode": "none"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["report"]["format"] == "yolo-detection"
    assert body["report"]["usableImages"] == 1
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["imageId"] == iid
    assert item["labeled"] is True
    assert item["split"] == "train"


def test_validate_default_split_when_omitted(client: TestClient) -> None:
    pid, lsid, _ = _setup_labelset(client)
    r = client.post(
        f"/v1/projects/{pid}/labelsets/{lsid}/export/validate",
        json={},
    )
    assert r.status_code == 200
    assert r.json()["report"]["splitCounts"]["train"] == 1


def test_validate_missing_returns_404(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "P"}).json()["project"]["id"]
    r = client.post(
        f"/v1/projects/{pid}/labelsets/nope/export/validate",
        json={},
    )
    assert r.status_code == 404
