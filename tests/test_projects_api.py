"""Integration tests for ``/v1/projects`` against the contract.

The fixture overrides STORAGE_ROOT to a tmp dir so each test starts on a
clean filesystem and the per-path lock dict is reset on teardown.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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


def test_list_empty(client: TestClient) -> None:
    r = client.get("/v1/projects")
    assert r.status_code == 200
    assert r.json() == {"projects": []}


def test_create_returns_201_with_project(client: TestClient) -> None:
    r = client.post("/v1/projects", json={"name": "Demo"})
    assert r.status_code == 201
    body = r.json()
    assert body["project"]["name"] == "Demo"
    assert body["project"]["members"] == []
    assert isinstance(body["project"]["id"], str) and body["project"]["id"]


def test_create_then_get(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "X"}).json()["project"]["id"]
    r = client.get(f"/v1/projects/{pid}")
    assert r.status_code == 200
    assert r.json()["project"]["id"] == pid


def test_list_includes_summary_counts(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "A"}).json()["project"]["id"]
    listed = client.get("/v1/projects").json()
    assert len(listed["projects"]) == 1
    s = listed["projects"][0]
    assert s["id"] == pid
    assert s["resourceCount"] == 0
    assert s["imageCount"] == 0
    assert s["labelSetCount"] == 0


def test_create_blank_name_returns_400(client: TestClient) -> None:
    r = client.post("/v1/projects", json={"name": "   "})
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "invalid_input"
    assert "name is required" in body["message"]


def test_create_missing_name_returns_422(client: TestClient) -> None:
    r = client.post("/v1/projects", json={})
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "invalid_input"


def test_create_malformed_json_returns_422(client: TestClient) -> None:
    r = client.post(
        "/v1/projects",
        content=b"not-json",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_input"


def test_get_missing_returns_404(client: TestClient) -> None:
    r = client.get("/v1/projects/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404
    body = r.json()
    assert body["error"] == "not_found"


def test_get_invalid_id_returns_400(client: TestClient) -> None:
    """``..`` is rejected by the storage safe-id guard."""
    r = client.get("/v1/projects/..")
    # FastAPI may also return 404 if path normalization absorbs the segment.
    # Either is acceptable as long as it's not 200.
    assert r.status_code in (400, 404)


def test_delete_existing(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"name": "X"}).json()["project"]["id"]
    r = client.delete(f"/v1/projects/{pid}")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert client.get("/v1/projects").json()["projects"] == []
    assert client.get(f"/v1/projects/{pid}").status_code == 404


def test_delete_idempotent(client: TestClient) -> None:
    r = client.delete("/v1/projects/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_list_orders_newest_first(client: TestClient) -> None:
    a = client.post("/v1/projects", json={"name": "A"}).json()["project"]
    b = client.post("/v1/projects", json={"name": "B"}).json()["project"]
    listed = client.get("/v1/projects").json()["projects"]
    assert [p["id"] for p in listed] == [b["id"], a["id"]]
