"""Project repository for database operations."""

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from stt_service.db.models import Job, JobStatus, Project
from stt_service.utils.exceptions import ProjectNotFoundError


class ProjectRepository:
    """Repository for Project database operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        name: str,
        description: str | None = None,
    ) -> Project:
        """Create a new project."""
        project = Project(
            name=name,
            description=description,
        )
        self.session.add(project)
        await self.session.flush()
        await self.session.refresh(project)
        return project

    async def get_by_id(self, project_id: str) -> Project:
        """Get project by ID."""
        query = select(Project).where(Project.id == project_id)
        result = await self.session.execute(query)
        project = result.scalar_one_or_none()

        if not project:
            raise ProjectNotFoundError(f"Project not found: {project_id}")

        return project

    async def list_projects(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Project]:
        """List projects ordered by most recently updated."""
        query = (
            select(Project)
            .order_by(Project.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def count_projects(self) -> int:
        """Count total projects."""
        query = select(func.count(Project.id))
        result = await self.session.execute(query)
        return result.scalar() or 0

    async def update(
        self,
        project_id: str,
        name: str | None = None,
        description: str | None = None,
    ) -> Project:
        """Update project fields."""
        updates: dict[str, Any] = {}
        if name is not None:
            updates["name"] = name
        if description is not None:
            updates["description"] = description

        if updates:
            stmt = update(Project).where(Project.id == project_id).values(**updates)
            await self.session.execute(stmt)
            await self.session.flush()

        return await self.get_by_id(project_id)

    async def delete(self, project_id: str) -> None:
        """Delete a project. Jobs are preserved (project_id set to NULL via SET NULL FK)."""
        project = await self.get_by_id(project_id)
        await self.session.delete(project)
        await self.session.flush()

    async def recalculate_cost(self, project_id: str) -> float:
        """Recalculate total cost from completed jobs' usage data.

        Sums usage.total_cost_usd from the result JSON of completed jobs.
        """
        query = (
            select(Job.result)
            .where(
                Job.project_id == project_id,
                Job.status == JobStatus.COMPLETED,
                Job.result.isnot(None),
            )
        )
        result = await self.session.execute(query)
        rows = result.scalars().all()

        total_cost = 0.0
        for job_result in rows:
            if job_result and isinstance(job_result, dict):
                usage = job_result.get("usage")
                if usage and isinstance(usage, dict):
                    total_cost += usage.get("total_cost_usd", 0.0)

        stmt = (
            update(Project)
            .where(Project.id == project_id)
            .values(total_cost_usd=total_cost)
        )
        await self.session.execute(stmt)
        await self.session.flush()

        return total_cost

    async def get_job_count(self, project_id: str) -> int:
        """Get number of jobs in a project."""
        query = select(func.count(Job.id)).where(Job.project_id == project_id)
        result = await self.session.execute(query)
        return result.scalar() or 0
