"""Job orchestration for transcription workflow."""

import os
import tempfile
from typing import Any

import structlog

from stt_service.config import get_settings
from stt_service.core.chunker import AudioChunker
from stt_service.db.models import JobStatus
from stt_service.db.repositories.chunk import ChunkRepository
from stt_service.db.repositories.job import JobRepository
from stt_service.services.storage import StorageService
from stt_service.workers.tasks import process_transcription_job

logger = structlog.get_logger()
settings = get_settings()


class JobOrchestrator:
    """Orchestrates the transcription job workflow.

    Responsibilities:
    - Job creation and validation
    - File upload handling
    - Job submission to workers
    - Progress tracking
    - Retry management
    """

    def __init__(
        self,
        job_repo: JobRepository,
        chunk_repo: ChunkRepository,
        storage: StorageService,
    ) -> None:
        self.job_repo = job_repo
        self.chunk_repo = chunk_repo
        self.storage = storage
        self.chunker = AudioChunker()

    async def create_job(
        self,
        config: dict[str, Any],
        provider: str,
        filename: str | None = None,
        file_size: int | None = None,
        webhook_url: str | None = None,
        project_id: str | None = None,
    ) -> str:
        """Create a new transcription job.

        Args:
            config: Transcription configuration
            provider: STT provider name
            filename: Original filename
            file_size: File size in bytes
            webhook_url: Optional webhook URL
            project_id: Optional project to associate with

        Returns:
            Job ID
        """
        job = await self.job_repo.create(
            config=config,
            provider=provider,
            original_filename=filename,
            file_size_bytes=file_size,
            webhook_url=webhook_url,
            project_id=project_id,
        )

        logger.info(
            "Created transcription job",
            job_id=job.id,
            provider=provider,
            filename=filename,
        )

        return job.id

    async def upload_audio(
        self,
        job_id: str,
        audio_data: bytes,
        filename: str,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        """Upload audio file for a job.

        Args:
            job_id: Job ID
            audio_data: Audio file bytes
            filename: Original filename
            content_type: MIME type

        Returns:
            Upload result with S3 key and metadata
        """
        # Generate S3 key
        s3_key = self.storage.generate_job_key(job_id, filename)

        # Upload to S3
        await self.storage.upload_file(
            s3_key,
            audio_data,
            content_type=content_type or "audio/mpeg",
            metadata={"job_id": job_id, "original_filename": filename},
        )

        # Get audio metadata
        with tempfile.NamedTemporaryFile(delete=False, suffix=self._get_extension(filename)) as f:
            f.write(audio_data)
            temp_path = f.name

        try:
            metadata = await self.chunker.get_audio_metadata(temp_path)

            # Update job with file info
            await self.job_repo.update_file_info(
                job_id,
                s3_original_key=s3_key,
                duration_seconds=metadata.duration,
                audio_format=metadata.format,
            )

            logger.info(
                "Uploaded audio for job",
                job_id=job_id,
                s3_key=s3_key,
                duration=metadata.duration,
                format=metadata.format,
            )

            return {
                "s3_key": s3_key,
                "duration_seconds": metadata.duration,
                "format": metadata.format,
                "sample_rate": metadata.sample_rate,
                "channels": metadata.channels,
            }

        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    async def submit_job(self, job_id: str) -> dict[str, Any]:
        """Submit a job for processing.

        Args:
            job_id: Job ID to submit

        Returns:
            Submission result
        """
        job = await self.job_repo.get_by_id(job_id)

        if job.status not in [JobStatus.PENDING, JobStatus.UPLOADED]:
            raise ValueError(f"Job cannot be submitted in state: {job.status}")

        if not job.s3_original_key:
            raise ValueError("No audio file uploaded for this job")

        # Queue the job for processing
        task = process_transcription_job.delay(job_id)

        logger.info(
            "Submitted job for processing",
            job_id=job_id,
            task_id=task.id,
        )

        return {
            "job_id": job_id,
            "task_id": task.id,
            "status": "submitted",
        }

    async def get_job_status(self, job_id: str) -> dict[str, Any]:
        """Get current job status and progress.

        Args:
            job_id: Job ID

        Returns:
            Status dict with progress info
        """
        job = await self.job_repo.get_by_id(job_id)

        progress_percent = 0.0
        if job.total_chunks > 0:
            progress_percent = (job.completed_chunks / job.total_chunks) * 100

        return {
            "job_id": job.id,
            "status": str(job.status.value),
            "provider": job.provider,
            "total_chunks": job.total_chunks,
            "completed_chunks": job.completed_chunks,
            "progress_percent": round(progress_percent, 1),
            "duration_seconds": job.duration_seconds,
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat(),
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "error_message": job.error_message,
        }

    async def get_job_progress(self, job_id: str) -> dict[str, Any]:
        """Get detailed job progress including chunk status.

        Args:
            job_id: Job ID

        Returns:
            Detailed progress dict
        """
        job = await self.job_repo.get_by_id(job_id, include_chunks=True)

        chunks = [
            {
                "index": c.chunk_index,
                "status": str(c.status.value),
                "start_time": c.start_time,
                "end_time": c.end_time,
                "attempt_count": c.attempt_count,
                "error": c.last_error,
            }
            for c in job.chunks
        ]

        failed_chunks = sum(1 for c in chunks if c["status"] == "failed")
        progress_percent = 0.0
        if job.total_chunks > 0:
            progress_percent = (job.completed_chunks / job.total_chunks) * 100

        return {
            "job_id": job.id,
            "status": str(job.status.value),
            "total_chunks": job.total_chunks,
            "completed_chunks": job.completed_chunks,
            "failed_chunks": failed_chunks,
            "progress_percent": round(progress_percent, 1),
            "chunks": chunks,
        }

    async def get_job_result(self, job_id: str) -> dict[str, Any] | None:
        """Get job result if completed.

        Args:
            job_id: Job ID

        Returns:
            Result dict or None if not completed
        """
        job = await self.job_repo.get_by_id(job_id)

        if job.status != JobStatus.COMPLETED:
            return None

        return job.result

    async def retry_job(self, job_id: str) -> dict[str, Any]:
        """Retry a failed job from last checkpoint.

        Args:
            job_id: Job ID to retry

        Returns:
            Retry result
        """
        job = await self.job_repo.get_by_id(job_id)

        if job.status != JobStatus.FAILED:
            raise ValueError(f"Can only retry FAILED jobs, current status: {job.status}")

        # Reset failed chunks to pending
        reset_count = await self.chunk_repo.reset_failed_chunks(job_id)

        # Update job status
        await self.job_repo.update_status(job_id, JobStatus.PROCESSING)

        # Resubmit for processing
        task = process_transcription_job.delay(job_id)

        logger.info(
            "Retrying failed job",
            job_id=job_id,
            reset_chunks=reset_count,
            task_id=task.id,
        )

        return {
            "job_id": job_id,
            "task_id": task.id,
            "reset_chunks": reset_count,
            "status": "retrying",
        }

    async def cancel_job(self, job_id: str) -> dict[str, Any]:
        """Cancel a running job.

        Args:
            job_id: Job ID to cancel

        Returns:
            Cancellation result
        """
        job = await self.job_repo.get_by_id(job_id)

        if job.status in [JobStatus.COMPLETED, JobStatus.CANCELLED]:
            raise ValueError(f"Cannot cancel job in state: {job.status}")

        await self.job_repo.update_status(job_id, JobStatus.CANCELLED)

        logger.info("Cancelled job", job_id=job_id)

        return {
            "job_id": job_id,
            "status": "cancelled",
        }

    async def delete_job(self, job_id: str) -> dict[str, Any]:
        """Delete a job and its associated files.

        Args:
            job_id: Job ID to delete

        Returns:
            Deletion result
        """
        job = await self.job_repo.get_by_id(job_id, include_chunks=True)

        # Collect S3 keys to delete
        keys_to_delete = []
        if job.s3_original_key:
            keys_to_delete.append(job.s3_original_key)
        if job.s3_result_key:
            keys_to_delete.append(job.s3_result_key)
        for chunk in job.chunks:
            if chunk.s3_chunk_key:
                keys_to_delete.append(chunk.s3_chunk_key)

        # Delete from S3
        if keys_to_delete:
            await self.storage.delete_files(keys_to_delete)

        # Delete from database
        await self.job_repo.delete(job_id)

        logger.info(
            "Deleted job",
            job_id=job_id,
            deleted_files=len(keys_to_delete),
        )

        return {
            "job_id": job_id,
            "deleted_files": len(keys_to_delete),
            "status": "deleted",
        }

    @staticmethod
    def _get_extension(filename: str) -> str:
        """Get file extension with dot."""
        if "." in filename:
            return "." + filename.rsplit(".", 1)[1].lower()
        return ".wav"
