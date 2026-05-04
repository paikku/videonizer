"""Project domain models — request/response shapes for /api/projects."""
from __future__ import annotations

from pydantic import Field

from .common import ApiModel


class ProjectCreate(ApiModel):
    name: str = Field(..., min_length=1, max_length=200)


class Project(ApiModel):
    id: str
    name: str
    created_at: int


class ProjectSummary(Project):
    """List view. Counts are computed via aggregation queries on demand."""

    resource_count: int = 0
    image_count: int = 0
    label_set_count: int = 0


class ProjectListResponse(ApiModel):
    projects: list[ProjectSummary]


class ProjectResponse(ApiModel):
    project: Project


class OkResponse(ApiModel):
    ok: bool = True
