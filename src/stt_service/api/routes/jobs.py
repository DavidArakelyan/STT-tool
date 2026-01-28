"""Jobs API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from stt_service.api.dependencies import (
    APIKey,
    ChunkRepo,
    JobRepo,
    Storage,
)
from stt_service.api.schemas.job import MessageResponse
from stt_service.api.schemas.transcription import (
    ChunkProgress,
    JobListResponse,
    JobProgress,
    JobResponse,
    JobStatus,
    Speaker,
    Transcript,
    TranscriptSegment,
    TranscriptionResult,
)
from stt_service.core.orchestrator import JobOrchestrator
from stt_service.db.models import JobStatus as DBJobStatus

router = APIRouter(prefix="/jobs", tags=["Jobs"])


def get_orchestrator(
    job_repo: JobRepo,
    chunk_repo: ChunkRepo,
    storage: Storage,
) -> JobOrchestrator:
    """Get job orchestrator instance."""
    return JobOrchestrator(job_repo, chunk_repo, storage)


@router.get("", response_model=JobListResponse)
async def list_jobs(
    job_repo: JobRepo,
    _api_key: APIKey,
    status: JobStatus | None = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=100, description="Maximum results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
) -> JobListResponse:
    """List transcription jobs with optional filtering."""
    db_status = None
    if status:
        db_status = DBJobStatus(status.value)

    jobs = await job_repo.list_jobs(status=db_status, limit=limit, offset=offset)
    total = await job_repo.count_jobs(status=db_status)

    return JobListResponse(
        jobs=[
            JobResponse(
                job_id=job.id,
                status=JobStatus(job.status.value),
                original_filename=job.original_filename,
                file_size_bytes=job.file_size_bytes,
                duration_seconds=job.duration_seconds,
                provider=job.provider,
                total_chunks=job.total_chunks,
                completed_chunks=job.completed_chunks,
                created_at=job.created_at,
                updated_at=job.updated_at,
                completed_at=job.completed_at,
                error_message=job.error_message,
            )
            for job in jobs
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    job_repo: JobRepo,
    _api_key: APIKey,
) -> JobResponse:
    """Get job status and metadata."""
    job = await job_repo.get_by_id(job_id)

    return JobResponse(
        job_id=job.id,
        status=JobStatus(job.status.value),
        original_filename=job.original_filename,
        file_size_bytes=job.file_size_bytes,
        duration_seconds=job.duration_seconds,
        provider=job.provider,
        total_chunks=job.total_chunks,
        completed_chunks=job.completed_chunks,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
        error_message=job.error_message,
    )


@router.get("/{job_id}/progress", response_model=JobProgress)
async def get_job_progress(
    job_id: str,
    _api_key: APIKey,
    orchestrator: JobOrchestrator = Depends(get_orchestrator),
    include_chunks: bool = Query(False, description="Include individual chunk status"),
) -> JobProgress:
    """Get detailed job progress including chunk status."""
    progress = await orchestrator.get_job_progress(job_id)

    chunks = None
    if include_chunks and progress.get("chunks"):
        chunks = [
            ChunkProgress(
                chunk_index=c["index"],
                status=c["status"],
                start_time=c["start_time"],
                end_time=c["end_time"],
                attempt_count=c["attempt_count"],
                error=c.get("error"),
            )
            for c in progress["chunks"]
        ]

    return JobProgress(
        job_id=progress["job_id"],
        status=JobStatus(progress["status"]),
        total_chunks=progress["total_chunks"],
        completed_chunks=progress["completed_chunks"],
        failed_chunks=progress["failed_chunks"],
        progress_percent=progress["progress_percent"],
        chunks=chunks,
    )


@router.get("/{job_id}/result", response_model=TranscriptionResult)
async def get_job_result(
    job_id: str,
    job_repo: JobRepo,
    _api_key: APIKey,
) -> TranscriptionResult:
    """Get transcription result for a completed job."""
    job = await job_repo.get_by_id(job_id)

    if job.status != DBJobStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job not completed. Current status: {job.status.value}",
        )

    if not job.result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Result not found for this job",
        )

    result = job.result

    transcript = Transcript(
        full_text=result.get("full_text", ""),
        segments=[
            TranscriptSegment(
                speaker_id=s.get("speaker_id", "SPEAKER_00"),
                speaker_label=s.get("speaker_label"),
                start_time=s.get("start_time", 0),
                end_time=s.get("end_time", 0),
                text=s.get("text", ""),
                confidence=s.get("confidence"),
            )
            for s in result.get("segments", [])
        ],
        speakers=[
            Speaker(
                speaker_id=sp.get("speaker_id", "SPEAKER_00"),
                label=sp.get("label"),
                total_duration=sp.get("total_duration", 0),
                segment_count=sp.get("segment_count", 0),
            )
            for sp in result.get("speakers", [])
        ],
    )

    # Calculate processing time
    processing_time = 0.0
    if job.completed_at and job.created_at:
        processing_time = (job.completed_at - job.created_at).total_seconds()

    return TranscriptionResult(
        job_id=job.id,
        status=JobStatus.COMPLETED,
        duration_seconds=job.duration_seconds or 0,
        language_detected=result.get("metadata", {}).get("language_detected"),
        provider_used=job.provider or "unknown",
        transcript=transcript,
        processing_time_seconds=processing_time,
        chunks_processed=job.completed_chunks,
        warnings=result.get("warnings", []),
    )


@router.post("/{job_id}/retry", response_model=MessageResponse)
async def retry_job(
    job_id: str,
    _api_key: APIKey,
    orchestrator: JobOrchestrator = Depends(get_orchestrator),
) -> MessageResponse:
    """Retry a failed job from the last successful checkpoint."""
    try:
        result = await orchestrator.retry_job(job_id)
        return MessageResponse(
            message=f"Job {job_id} queued for retry. {result['reset_chunks']} chunks reset."
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.delete("/{job_id}", response_model=MessageResponse)
async def delete_job(
    job_id: str,
    _api_key: APIKey,
    orchestrator: JobOrchestrator = Depends(get_orchestrator),
) -> MessageResponse:
    """Delete a job and all associated files."""
    result = await orchestrator.delete_job(job_id)
    return MessageResponse(
        message=f"Job {job_id} deleted. {result['deleted_files']} files removed."
    )


@router.post("/{job_id}/cancel", response_model=MessageResponse)
async def cancel_job(
    job_id: str,
    _api_key: APIKey,
    orchestrator: JobOrchestrator = Depends(get_orchestrator),
) -> MessageResponse:
    """Cancel a running job."""
    try:
        await orchestrator.cancel_job(job_id)
        return MessageResponse(message=f"Job {job_id} cancelled.")
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
