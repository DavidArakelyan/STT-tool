"""Jobs API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
import io
import zipfile
import json
import shutil
from pathlib import Path

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


@router.get("/{job_id}/logs")
async def get_job_logs(
    job_id: str,
    job_repo: JobRepo,
    chunk_repo: ChunkRepo,
    _api_key: APIKey,
) -> dict:
    """Get detailed job logs and processing history."""
    job = await job_repo.get_by_id(job_id)
    chunks = await chunk_repo.get_chunks_for_job(job_id)

    # Build log entries
    log_entries = []

    # Job created
    log_entries.append({
        "timestamp": job.created_at.isoformat(),
        "level": "info",
        "message": f"Job created - File: {job.original_filename}",
    })

    # File uploaded
    if job.s3_original_key:
        log_entries.append({
            "timestamp": job.created_at.isoformat(),
            "level": "info",
            "message": f"File uploaded to storage ({job.file_size_bytes} bytes)",
        })

    # Chunks created
    if job.total_chunks > 0:
        log_entries.append({
            "timestamp": job.created_at.isoformat(),
            "level": "info",
            "message": f"Audio split into {job.total_chunks} chunks for processing",
        })

    # Chunk processing details
    for chunk in sorted(chunks, key=lambda c: c.chunk_index):
        if chunk.status.value == "completed" and chunk.processed_at:
            log_entries.append({
                "timestamp": chunk.processed_at.isoformat(),
                "level": "success",
                "message": f"✅ Chunk {chunk.chunk_index + 1} finalized (Duration: {chunk.end_time - chunk.start_time:.1f}s)",
            })
        elif chunk.status.value == "failed":
            log_entries.append({
                "timestamp": chunk.processed_at.isoformat() if chunk.processed_at else job.updated_at.isoformat(),
                "level": "error",
                "message": f"❌ Chunk {chunk.chunk_index + 1} failed (Attempt {chunk.attempt_count}): {chunk.last_error or 'Unknown error'}",
            })
        elif chunk.status.value == "processing":
            # Use job updated_at as a proxy for current activity if chunk doesn't have its own processing_start
            log_entries.append({
                "timestamp": job.updated_at.isoformat(),
                "level": "info",
                "message": f"⚙️ Chunk {chunk.chunk_index + 1} is being processed by {job.provider.upper()} (Attempt {chunk.attempt_count})",
            })

    # Job error
    if job.error_message:
        log_entries.append({
            "timestamp": job.updated_at.isoformat(),
            "level": "error",
            "message": f"🛑 Job halted: {job.error_message}",
        })

    # Job completed
    if job.completed_at:
        log_entries.append({
            "timestamp": job.completed_at.isoformat(),
            "level": "success",
            "message": f"✨ Job completed successfully! Total processing time: {(job.completed_at - job.created_at).total_seconds():.1f}s",
        })

    # Sort by timestamp
    log_entries.sort(key=lambda x: x["timestamp"])

    return {
        "job_id": job.id,
        "status": job.status.value,
        "provider": job.provider,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "total_chunks": job.total_chunks,
        "completed_chunks": job.completed_chunks,
        "logs": log_entries,
    }


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
        original_filename=job.original_filename,
        language_detected=result.get("metadata", {}).get("language_detected"),
        provider_used=job.provider or "unknown",
        transcript=transcript,
        processing_time_seconds=processing_time,
        chunks_processed=job.completed_chunks,
        warnings=result.get("warnings", []),
    )



@router.get("/{job_id}/download-bundle")
async def download_bundle(
    job_id: str,
    job_repo: JobRepo,
    storage: Storage,
    _api_key: APIKey,
) -> StreamingResponse:
    """Download a ZIP bundle containing the source audio and transcript."""
    from pathlib import Path

    job = await job_repo.get_by_id(job_id)

    if job.status != DBJobStatus.COMPLETED or not job.result:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job not ready. Status: {job.status.value}",
        )

    # 1. Get Audio File
    if not job.s3_original_key:
        raise HTTPException(status_code=404, detail="Audio file not found in storage record")

    try:
        audio_bytes = await storage.download_file(job.s3_original_key)
    except Exception as e:
         raise HTTPException(status_code=500, detail=f"Failed to retrieve audio: {e}")

    # 2. Get Transcript Text
    transcript_text = job.result.get("full_text", "")
    if not transcript_text:
        # Fallback to segments if full_text missing
        segments = job.result.get("segments", [])
        transcript_text = "\n".join([s.get("text", "") for s in segments])

    # 3. Get combined_transcript.json if exists
    combined_json_path = Path(f"logs/jobs/{job_id}/combined_transcript.json")
    combined_json_content = None
    if combined_json_path.exists():
        try:
            with open(combined_json_path, "r", encoding="utf-8") as f:
                combined_json_content = f.read()
        except Exception as e:
            # Log but don't fail the entire download
            import structlog
            logger = structlog.get_logger()
            logger.warning("Failed to read combined_transcript.json", job_id=job_id, error=str(e))

    # 4. Create ZIP in memory
    zip_buffer = io.BytesIO()
    base_name = job.original_filename.rsplit(".", 1)[0] if job.original_filename else job_id

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        # Add Audio
        zip_file.writestr(job.original_filename, audio_bytes)
        # Add Transcript (BOM for Excel/Windows compatibility)
        zip_file.writestr(f"{base_name}_transcript.txt", '\uFEFF' + transcript_text)
        # Add combined_transcript.json if available
        if combined_json_content:
            zip_file.writestr(f"{base_name}_combined_transcript.json", combined_json_content)

    zip_buffer.seek(0)

    zip_buffer.seek(0)
    
    from urllib.parse import quote
    encoded_filename = quote(f"{base_name}.zip")

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
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
    
    # Also delete logs directory
    try:
        log_dir = Path(f"logs/jobs/{job_id}")
        if log_dir.exists() and log_dir.is_dir():
            shutil.rmtree(log_dir)
            result['deleted_files'] += 1  # Count log dir as a file/resource
    except Exception as e:
        # Log error but don't fail the request
        import structlog
        logger = structlog.get_logger()
        logger.warning(f"Failed to delete log directory for job {job_id}", error=str(e))

    return MessageResponse(
        message=f"Job {job_id} deleted. {result['deleted_files']} files removed."
    )


@router.delete("", response_model=MessageResponse)
async def delete_all_jobs(
    job_repo: JobRepo,
    _api_key: APIKey,
    background_tasks: bool = Query(False, description="Run deletion in background (not implemented yet)"),
    orchestrator: JobOrchestrator = Depends(get_orchestrator),
) -> MessageResponse:
    """Delete ALL jobs and associated files."""
    # List all jobs (no limit)
    # Note: For very large numbers of jobs, this should be paginated or backgrounded
    # But for a personal tool, iterating 100-200 jobs is fine.
    jobs = await job_repo.list_jobs(limit=1000)
    
    deleted_count = 0
    errors = []
    
    for job in jobs:
        try:
            await orchestrator.delete_job(job.id)
            # Delete logs
            log_dir = Path(f"logs/jobs/{job.id}")
            if log_dir.exists() and log_dir.is_dir():
                shutil.rmtree(log_dir)
            deleted_count += 1
        except Exception as e:
            errors.append(f"{job.id}: {str(e)}")
            
    msg = f"Deleted {deleted_count} jobs."
    if errors:
        msg += f" Failed to delete {len(errors)} jobs."
        
    return MessageResponse(message=msg)


@router.get("/{job_id}/system-logs")
async def get_system_logs(
    job_id: str,
    _api_key: APIKey,
    limit: int = Query(200, ge=1, le=1000, description="Max log lines to return"),
) -> dict:
    """Get developer-level system logs for a specific job."""
    from pathlib import Path
    
    log_file = Path("/app/logs/app.log")
    if not log_file.exists():
        return {"job_id": job_id, "logs": [], "error": "Log file not found"}

    logs = []
    try:
        # Read the file from the end for efficiency
        # For simplicity, we'll read the last 10k lines and filter
        # In a high-traffic system, a more efficient 'grep' or 'tail' approach would be better
        with open(log_file, "r") as f:
            # Simple tail: read last 10000 lines
            # This is safe because it's dev-only and log file is usually rotated
            all_lines = f.readlines()
            # Filter lines containing job_id
            for line in reversed(all_lines):
                if job_id in line:
                    logs.append(line.strip())
                    if len(logs) >= limit:
                        break
        
        # Return in chronological order
        logs.reverse()
    except Exception as e:
        return {"job_id": job_id, "logs": [], "error": str(e)}

    return {
        "job_id": job_id,
        "logs": logs,
        "total": len(logs)
    }


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


@router.get("/{job_id}/chunks/{chunk_index}/log")
async def get_chunk_log(
    job_id: str,
    chunk_index: int,
    _api_key: APIKey,
) -> dict:
    """Get the raw JSON log/result for a specific chunk."""
    try:
        # Construct path to chunk log file
        # Pattern: logs/jobs/{job_id}/chunk_{index}.json
        # NOTE: The worker saves it as f"chunk_{chunk.chunk_index}.json" inside f"logs/jobs/{job_id}"
        log_path = Path(f"logs/jobs/{job_id}/chunk_{chunk_index}.json")
        
        if not log_path.exists():
             raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Log file for chunk {chunk_index} not found",
            )
            
        with open(log_path, "r", encoding="utf-8") as f:
            return json.load(f)
            
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read chunk log: {str(e)}",
        )
