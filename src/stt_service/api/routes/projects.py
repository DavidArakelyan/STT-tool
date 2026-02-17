"""Projects API endpoints."""

from fastapi import APIRouter, Query

from stt_service.api.dependencies import APIKey, JobRepo, ProjectRepo
from stt_service.api.schemas.job import MessageResponse
from stt_service.api.schemas.project import (
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdate,
)

router = APIRouter(prefix="/projects", tags=["Projects"])


@router.post("", response_model=ProjectResponse)
async def create_project(
    body: ProjectCreate,
    project_repo: ProjectRepo,
    _api_key: APIKey,
) -> ProjectResponse:
    """Create a new project."""
    project = await project_repo.create(
        name=body.name,
        description=body.description,
    )

    return ProjectResponse(
        project_id=project.id,
        name=project.name,
        description=project.description,
        total_cost_usd=project.total_cost_usd,
        job_count=0,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    project_repo: ProjectRepo,
    _api_key: APIKey,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ProjectListResponse:
    """List all projects."""
    projects = await project_repo.list_projects(limit=limit, offset=offset)
    total = await project_repo.count_projects()

    project_responses = []
    for p in projects:
        job_count = await project_repo.get_job_count(p.id)
        project_responses.append(
            ProjectResponse(
                project_id=p.id,
                name=p.name,
                description=p.description,
                total_cost_usd=p.total_cost_usd,
                job_count=job_count,
                created_at=p.created_at,
                updated_at=p.updated_at,
            )
        )

    return ProjectListResponse(
        projects=project_responses,
        total=total,
    )


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    project_repo: ProjectRepo,
    _api_key: APIKey,
) -> ProjectResponse:
    """Get a single project by ID."""
    project = await project_repo.get_by_id(project_id)
    job_count = await project_repo.get_job_count(project_id)

    return ProjectResponse(
        project_id=project.id,
        name=project.name,
        description=project.description,
        total_cost_usd=project.total_cost_usd,
        job_count=job_count,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    body: ProjectUpdate,
    project_repo: ProjectRepo,
    _api_key: APIKey,
) -> ProjectResponse:
    """Update a project."""
    project = await project_repo.update(
        project_id,
        name=body.name,
        description=body.description,
    )
    job_count = await project_repo.get_job_count(project_id)

    return ProjectResponse(
        project_id=project.id,
        name=project.name,
        description=project.description,
        total_cost_usd=project.total_cost_usd,
        job_count=job_count,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.delete("/{project_id}", response_model=MessageResponse)
async def delete_project(
    project_id: str,
    project_repo: ProjectRepo,
    _api_key: APIKey,
) -> MessageResponse:
    """Delete a project. Jobs are preserved (unlinked from project)."""
    await project_repo.delete(project_id)
    return MessageResponse(message=f"Project {project_id} deleted. Jobs have been preserved.")
