"""Project routes — /api/projects.

Lists, creates, fetches, deletes. Counts on ``ProjectSummary`` are stubbed
to 0 in this PR; later PRs replace the stub queries with real aggregation
once the corresponding tables exist.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain.projects import (
    OkResponse,
    Project,
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectSummary,
)
from ..errors import NotFound
from ..storage.repo.projects import Project as ProjectRow, ProjectRepo
from ..storage.repo.resources import ResourceRepo
from ._deps import current_user_id, get_session

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _to_dto(row: ProjectRow) -> Project:
    return Project(id=row.id, name=row.name, created_at=row.created_at)


def _to_summary(row: ProjectRow, *, resource_count: int = 0) -> ProjectSummary:
    # image_count + label_set_count remain placeholders until PR #4 / #5.
    return ProjectSummary(
        id=row.id,
        name=row.name,
        created_at=row.created_at,
        resource_count=resource_count,
        image_count=0,
        label_set_count=0,
    )


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    session: AsyncSession = Depends(get_session),
    _user: str = Depends(current_user_id),
) -> ProjectListResponse:
    rows = await ProjectRepo(session).list_all()
    res_repo = ResourceRepo(session)
    summaries: list[ProjectSummary] = []
    for r in rows:
        # N+1 is fine for the small project counts we expect; can collapse
        # to a single GROUP BY later if it shows up in profiling.
        rc = await res_repo.count_for_project(r.id)
        summaries.append(_to_summary(r, resource_count=rc))
    return ProjectListResponse(projects=summaries)


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    body: ProjectCreate,
    session: AsyncSession = Depends(get_session),
    _user: str = Depends(current_user_id),
) -> ProjectResponse:
    row = await ProjectRepo(session).create(name=body.name.strip())
    return ProjectResponse(project=_to_dto(row))


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    _user: str = Depends(current_user_id),
) -> ProjectResponse:
    row = await ProjectRepo(session).get(project_id)
    if row is None:
        raise NotFound("project_not_found", f"project {project_id} not found")
    return ProjectResponse(project=_to_dto(row))


@router.delete("/{project_id}", response_model=OkResponse)
async def delete_project(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    _user: str = Depends(current_user_id),
) -> OkResponse:
    deleted = await ProjectRepo(session).delete(project_id)
    if not deleted:
        raise NotFound("project_not_found", f"project {project_id} not found")
    return OkResponse(ok=True)
