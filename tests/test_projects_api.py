"""Project CRUD tests against a real (sqlite + moto) stateful_app."""
from __future__ import annotations

import pytest


async def test_list_empty(stateful_app) -> None:
    r = await stateful_app.get("/api/projects")
    assert r.status_code == 200
    assert r.json() == {"projects": []}


async def test_create_and_get(stateful_app) -> None:
    r = await stateful_app.post("/api/projects", json={"name": "alpha"})
    assert r.status_code == 201, r.text
    body = r.json()["project"]
    assert body["name"] == "alpha"
    assert body["createdAt"] > 0
    pid = body["id"]
    assert len(pid) == 36  # UUID

    r2 = await stateful_app.get(f"/api/projects/{pid}")
    assert r2.status_code == 200
    assert r2.json()["project"]["id"] == pid


async def test_list_returns_summary_with_zero_counts(stateful_app) -> None:
    await stateful_app.post("/api/projects", json={"name": "alpha"})
    await stateful_app.post("/api/projects", json={"name": "beta"})

    r = await stateful_app.get("/api/projects")
    body = r.json()
    names = {p["name"] for p in body["projects"]}
    assert names == {"alpha", "beta"}
    for p in body["projects"]:
        assert p["resourceCount"] == 0
        assert p["imageCount"] == 0
        assert p["labelSetCount"] == 0


async def test_create_rejects_empty_name(stateful_app) -> None:
    r = await stateful_app.post("/api/projects", json={"name": ""})
    assert r.status_code == 422


async def test_get_missing_returns_404(stateful_app) -> None:
    r = await stateful_app.get("/api/projects/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404
    assert r.json()["error"] == "project_not_found"


async def test_delete(stateful_app) -> None:
    r = await stateful_app.post("/api/projects", json={"name": "doomed"})
    pid = r.json()["project"]["id"]

    r2 = await stateful_app.delete(f"/api/projects/{pid}")
    assert r2.status_code == 200
    assert r2.json() == {"ok": True}

    r3 = await stateful_app.get(f"/api/projects/{pid}")
    assert r3.status_code == 404


async def test_delete_missing_returns_404(stateful_app) -> None:
    r = await stateful_app.delete("/api/projects/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


async def test_listing_orders_by_created_at_desc(stateful_app) -> None:
    names = ["one", "two", "three"]
    for n in names:
        await stateful_app.post("/api/projects", json={"name": n})

    r = await stateful_app.get("/api/projects")
    returned = [p["name"] for p in r.json()["projects"]]
    # Most recent first.
    assert returned == ["three", "two", "one"]
