"""Job repository for database operations."""

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from stt_service.db.models import Chunk, ChunkStatus, Job, JobStatus
from stt_service.utils.exceptions import JobNotFoundError


class JobRepository:
    """Repository for Job database operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        config: dict[str, Any],
        provider: str,
        original_filename: str | None = None,
        file_size_bytes: int | None = None,
        webhook_url: str | None = None,
    ) -> Job:
        """Create a new job."""
        job = Job(
            config=config,
            provider=provider,
            original_filename=original_filename,
            file_size_bytes=file_size_bytes,
            webhook_url=webhook_url,
            status=JobStatus.PENDING,
        )
        self.session.add(job)
        await self.session.flush()
        await self.session.refresh(job)
        return job

    async def get_by_id(self, job_id: str, include_chunks: bool = False) -> Job:
        """Get job by ID."""
        query = select(Job).where(Job.id == job_id)
        if include_chunks:
            query = query.options(selectinload(Job.chunks))

        result = await self.session.execute(query)
        job = result.scalar_one_or_none()

        if not job:
            raise JobNotFoundError(f"Job not found: {job_id}")

        return job

    async def get_by_id_or_none(
        self, job_id: str, include_chunks: bool = False
    ) -> Job | None:
        """Get job by ID or return None."""
        query = select(Job).where(Job.id == job_id)
        if include_chunks:
            query = query.options(selectinload(Job.chunks))

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def list_jobs(
        self,
        status: JobStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Job]:
        """List jobs with optional filtering."""
        query = select(Job).order_by(Job.created_at.desc())

        if status:
            query = query.where(Job.status == status)

        query = query.limit(limit).offset(offset)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def count_jobs(self, status: JobStatus | None = None) -> int:
        """Count jobs with optional filtering."""
        query = select(func.count(Job.id))

        if status:
            query = query.where(Job.status == status)

        result = await self.session.execute(query)
        return result.scalar() or 0

    async def update_status(
        self,
        job_id: str,
        status: JobStatus,
        error_message: str | None = None,
        error_code: str | None = None,
    ) -> Job:
        """Update job status."""
        updates: dict[str, Any] = {"status": status}

        if error_message:
            updates["error_message"] = error_message

        if error_code:
            updates["error_code"] = error_code

        if status == JobStatus.COMPLETED:
            updates["completed_at"] = datetime.utcnow()

        stmt = update(Job).where(Job.id == job_id).values(**updates)
        await self.session.execute(stmt)
        await self.session.flush()

        return await self.get_by_id(job_id)

    async def update_file_info(
        self,
        job_id: str,
        s3_original_key: str,
        duration_seconds: float,
        audio_format: str,
    ) -> Job:
        """Update job with file information after upload."""
        stmt = (
            update(Job)
            .where(Job.id == job_id)
            .values(
                s3_original_key=s3_original_key,
                duration_seconds=duration_seconds,
                audio_format=audio_format,
                status=JobStatus.UPLOADED,
            )
        )
        await self.session.execute(stmt)
        await self.session.flush()

        return await self.get_by_id(job_id)

    async def update_chunk_counts(
        self,
        job_id: str,
        total_chunks: int | None = None,
        completed_chunks: int | None = None,
    ) -> Job:
        """Update chunk progress counts."""
        updates: dict[str, Any] = {}

        if total_chunks is not None:
            updates["total_chunks"] = total_chunks

        if completed_chunks is not None:
            updates["completed_chunks"] = completed_chunks

        if updates:
            stmt = update(Job).where(Job.id == job_id).values(**updates)
            await self.session.execute(stmt)
            await self.session.flush()

        return await self.get_by_id(job_id)

    async def increment_completed_chunks(self, job_id: str) -> Job:
        """Increment completed chunks count."""
        stmt = (
            update(Job)
            .where(Job.id == job_id)
            .values(completed_chunks=Job.completed_chunks + 1)
        )
        await self.session.execute(stmt)
        await self.session.flush()

        return await self.get_by_id(job_id)

    async def set_result(
        self,
        job_id: str,
        result: dict[str, Any],
        s3_result_key: str | None = None,
    ) -> Job:
        """Set job result."""
        updates: dict[str, Any] = {
            "result": result,
            "status": JobStatus.COMPLETED,
            "completed_at": datetime.utcnow(),
        }

        if s3_result_key:
            updates["s3_result_key"] = s3_result_key

        stmt = update(Job).where(Job.id == job_id).values(**updates)
        await self.session.execute(stmt)
        await self.session.flush()

        return await self.get_by_id(job_id)

    async def mark_webhook_sent(self, job_id: str) -> Job:
        """Mark webhook as sent."""
        stmt = update(Job).where(Job.id == job_id).values(webhook_sent=True)
        await self.session.execute(stmt)
        await self.session.flush()

        return await self.get_by_id(job_id)

    async def delete(self, job_id: str) -> None:
        """Delete a job (cascades to chunks)."""
        job = await self.get_by_id(job_id)
        await self.session.delete(job)
        await self.session.flush()

    async def fail_stale_jobs(self, stale_minutes: int = 30) -> int:
        """Mark long-running PROCESSING/UPLOADED jobs as FAILED.

        Returns the number of jobs that were marked as failed.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
        stmt = (
            update(Job)
            .where(
                Job.status.in_([JobStatus.PROCESSING, JobStatus.UPLOADED]),
                Job.updated_at < cutoff,
            )
            .values(
                status=JobStatus.FAILED,
                error_message=(
                    "Job timed out — likely interrupted by a service restart. "
                    "Please resubmit."
                ),
            )
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount

    async def get_expired_jobs(self, retention_days: int, batch_size: int = 50) -> list[Job]:
        """Return completed/failed jobs older than retention_days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        query = (
            select(Job)
            .where(
                Job.status.in_([JobStatus.COMPLETED, JobStatus.FAILED]),
                Job.created_at < cutoff,
            )
            .limit(batch_size)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_pending_chunks_count(self, job_id: str) -> int:
        """Get count of pending or failed chunks for a job."""
        query = (
            select(func.count(Chunk.id))
            .where(Chunk.job_id == job_id)
            .where(Chunk.status.in_([ChunkStatus.PENDING, ChunkStatus.FAILED]))
        )
        result = await self.session.execute(query)
        return result.scalar() or 0
