"""Projects API — top-level container CRUD.

Thin layer over ``app.storage`` that adapts the request/response shape to
the contract in ``API_CONTRACT.md`` §2. All cascade behavior is in
``storage`` so this module stays small.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from .. import storage
from ..errors import BadRequestError, NotFoundError

router = APIRouter(prefix="/v1/projects", tags=["projects"])


class CreateProjectBody(BaseModel):
    name: str


@router.get("")
async def list_projects() -> dict:
    return {"projects": await storage.list_projects()}


@router.post("", status_code=201)
async def create_project(body: CreateProjectBody) -> dict:
    name = (body.name or "").strip()
    if not name:
        raise BadRequestError("name is required")
    return {"project": await storage.create_project(name)}


@router.get("/{project_id}")
async def get_project(project_id: str) -> dict:
    project = await storage.get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    return {"project": project}


@router.delete("/{project_id}")
async def delete_project(project_id: str) -> dict:
    """Cascade-deletes the project and all of its sub-resources.

    Idempotent: deleting a missing id returns 200 without error.
    """
    await storage.delete_project(project_id)
    return {"ok": True}
