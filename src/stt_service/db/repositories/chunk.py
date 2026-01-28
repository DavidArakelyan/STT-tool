"""Chunk repository for database operations."""

from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from stt_service.db.models import Chunk, ChunkStatus


class ChunkRepository:
    """Repository for Chunk database operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        job_id: str,
        chunk_index: int,
        start_time: float,
        end_time: float,
        s3_chunk_key: str | None = None,
    ) -> Chunk:
        """Create a new chunk."""
        chunk = Chunk(
            job_id=job_id,
            chunk_index=chunk_index,
            start_time=start_time,
            end_time=end_time,
            s3_chunk_key=s3_chunk_key,
            status=ChunkStatus.PENDING,
        )
        self.session.add(chunk)
        await self.session.flush()
        await self.session.refresh(chunk)
        return chunk

    async def create_many(
        self,
        job_id: str,
        chunks_data: list[dict[str, Any]],
    ) -> list[Chunk]:
        """Create multiple chunks at once."""
        chunks = [
            Chunk(
                job_id=job_id,
                chunk_index=data["chunk_index"],
                start_time=data["start_time"],
                end_time=data["end_time"],
                s3_chunk_key=data.get("s3_chunk_key"),
                status=ChunkStatus.PENDING,
            )
            for data in chunks_data
        ]
        self.session.add_all(chunks)
        await self.session.flush()

        for chunk in chunks:
            await self.session.refresh(chunk)

        return chunks

    async def get_by_id(self, chunk_id: str) -> Chunk | None:
        """Get chunk by ID."""
        query = select(Chunk).where(Chunk.id == chunk_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_by_job_and_index(self, job_id: str, chunk_index: int) -> Chunk | None:
        """Get chunk by job ID and index."""
        query = select(Chunk).where(
            Chunk.job_id == job_id,
            Chunk.chunk_index == chunk_index,
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_chunks_for_job(
        self,
        job_id: str,
        status: ChunkStatus | None = None,
    ) -> list[Chunk]:
        """Get all chunks for a job."""
        query = (
            select(Chunk)
            .where(Chunk.job_id == job_id)
            .order_by(Chunk.chunk_index)
        )

        if status:
            query = query.where(Chunk.status == status)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_pending_chunks(self, job_id: str) -> list[Chunk]:
        """Get pending or failed chunks for retry."""
        query = (
            select(Chunk)
            .where(Chunk.job_id == job_id)
            .where(Chunk.status.in_([ChunkStatus.PENDING, ChunkStatus.FAILED]))
            .order_by(Chunk.chunk_index)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_next_pending_chunk(self, job_id: str) -> Chunk | None:
        """Get the next pending chunk to process."""
        query = (
            select(Chunk)
            .where(Chunk.job_id == job_id)
            .where(Chunk.status == ChunkStatus.PENDING)
            .order_by(Chunk.chunk_index)
            .limit(1)
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def update_status(
        self,
        chunk_id: str,
        status: ChunkStatus,
        error: str | None = None,
    ) -> Chunk | None:
        """Update chunk status."""
        updates: dict[str, Any] = {"status": status}

        if error:
            updates["last_error"] = error

        if status == ChunkStatus.COMPLETED:
            updates["processed_at"] = datetime.utcnow()

        stmt = update(Chunk).where(Chunk.id == chunk_id).values(**updates)
        await self.session.execute(stmt)
        await self.session.flush()

        return await self.get_by_id(chunk_id)

    async def mark_processing(self, chunk_id: str) -> Chunk | None:
        """Mark chunk as processing and increment attempt count."""
        stmt = (
            update(Chunk)
            .where(Chunk.id == chunk_id)
            .values(
                status=ChunkStatus.PROCESSING,
                attempt_count=Chunk.attempt_count + 1,
            )
        )
        await self.session.execute(stmt)
        await self.session.flush()

        return await self.get_by_id(chunk_id)

    async def set_result(
        self,
        chunk_id: str,
        result: dict[str, Any],
    ) -> Chunk | None:
        """Set chunk transcription result."""
        stmt = (
            update(Chunk)
            .where(Chunk.id == chunk_id)
            .values(
                result=result,
                status=ChunkStatus.COMPLETED,
                processed_at=datetime.utcnow(),
            )
        )
        await self.session.execute(stmt)
        await self.session.flush()

        return await self.get_by_id(chunk_id)

    async def reset_failed_chunks(self, job_id: str) -> int:
        """Reset failed chunks to pending for retry."""
        stmt = (
            update(Chunk)
            .where(Chunk.job_id == job_id)
            .where(Chunk.status == ChunkStatus.FAILED)
            .values(status=ChunkStatus.PENDING, last_error=None)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()

        return result.rowcount  # type: ignore

    async def get_completed_results(self, job_id: str) -> list[dict[str, Any]]:
        """Get all completed chunk results in order."""
        query = (
            select(Chunk.result, Chunk.start_time, Chunk.end_time, Chunk.chunk_index)
            .where(Chunk.job_id == job_id)
            .where(Chunk.status == ChunkStatus.COMPLETED)
            .order_by(Chunk.chunk_index)
        )
        result = await self.session.execute(query)
        rows = result.all()

        return [
            {
                "result": row.result,
                "start_time": row.start_time,
                "end_time": row.end_time,
                "chunk_index": row.chunk_index,
            }
            for row in rows
            if row.result is not None
        ]
