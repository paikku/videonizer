"""Project CRUD.

Layout owned by this module:
  ``projects.json``                          { "projects": [<projectId>, ...] }
  ``{projectId}/project.json``               Project
  ``{projectId}/resources.json``             [<resourceId>, ...]
  ``{projectId}/images.json``                [<imageId>, ...]
  ``{projectId}/labelsets.json``             [<labelSetId>, ...]

The sub-resource indices are written empty on creation so list endpoints
for a brand-new project return ``[]`` instead of ``ENOENT``.
"""
from __future__ import annotations

import asyncio
import shutil
import time
from typing import Any

from .io import ensure_dir, read_json, with_file_lock, write_json
from .ids import gen_id
from .paths import (
    images_index,
    labelsets_index,
    project_dir,
    project_file,
    projects_index,
    resources_index,
)


async def list_projects() -> list[dict[str, Any]]:
    """All projects, newest first. Broken / orphaned ids are skipped silently."""
    index = await read_json(projects_index(), {"projects": []})
    out: list[dict[str, Any]] = []
    for pid in index.get("projects", []):
        try:
            summary = await get_project_summary(pid)
            if summary is not None:
                out.append(summary)
        except Exception:
            continue
    out.sort(key=lambda s: s["createdAt"], reverse=True)
    return out


async def get_project(project_id: str) -> dict[str, Any] | None:
    return await read_json(project_file(project_id), None)


async def get_project_summary(project_id: str) -> dict[str, Any] | None:
    proj = await get_project(project_id)
    if proj is None:
        return None
    resources, images, labelsets = await asyncio.gather(
        read_json(resources_index(project_id), []),
        read_json(images_index(project_id), []),
        read_json(labelsets_index(project_id), []),
    )
    return {
        "id": proj["id"],
        "name": proj["name"],
        "createdAt": proj["createdAt"],
        "resourceCount": len(resources),
        "imageCount": len(images),
        "labelSetCount": len(labelsets),
    }


async def create_project(name: str) -> dict[str, Any]:
    pid = gen_id()
    project = {
        "id": pid,
        "name": (name or "").strip() or "Untitled",
        "createdAt": int(time.time() * 1000),
        "members": [],
    }
    await ensure_dir(project_dir(pid))
    await write_json(project_file(pid), project)
    await write_json(resources_index(pid), [])
    await write_json(images_index(pid), [])
    await write_json(labelsets_index(pid), [])

    async def append() -> None:
        index = await read_json(projects_index(), {"projects": []})
        if pid not in index["projects"]:
            index["projects"].append(pid)
        await write_json(projects_index(), index)

    await with_file_lock(projects_index(), append)
    return project


async def delete_project(project_id: str) -> None:
    """Cascade-deletes the project directory and removes from the index.

    Idempotent — deleting a missing project is a no-op.
    """

    async def remove() -> None:
        index = await read_json(projects_index(), {"projects": []})
        index["projects"] = [x for x in index["projects"] if x != project_id]
        await write_json(projects_index(), index)

    await with_file_lock(projects_index(), remove)
    # Use the raw path; safe_id has already been validated upstream when the
    # caller derived ``project_id`` from a URL parameter.
    await asyncio.to_thread(
        shutil.rmtree, project_dir(project_id), ignore_errors=True
    )
