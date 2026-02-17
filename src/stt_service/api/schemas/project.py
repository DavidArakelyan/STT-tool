"""API schemas for project management."""

from datetime import datetime

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    """Request to create a new project."""

    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None


class ProjectUpdate(BaseModel):
    """Request to update an existing project."""

    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None


class ProjectResponse(BaseModel):
    """Response for a single project."""

    project_id: str
    name: str
    description: str | None = None
    total_cost_usd: float = 0.0
    job_count: int = 0
    created_at: datetime
    updated_at: datetime


class ProjectListResponse(BaseModel):
    """Response for listing projects."""

    projects: list[ProjectResponse]
    total: int
