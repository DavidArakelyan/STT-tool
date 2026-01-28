"""Celery background tasks for transcription processing."""

import asyncio
import os
import tempfile
from typing import Any

import httpx
import structlog

from stt_service.config import get_settings
from stt_service.core.chunker import AudioChunker, ChunkInfo
from stt_service.core.merger import TranscriptMerger
from stt_service.core.retry import RetryConfig, retry_with_backoff
from stt_service.db.models import ChunkStatus, JobStatus
from stt_service.db.repositories.chunk import ChunkRepository
from stt_service.db.repositories.job import JobRepository
from stt_service.db.session import get_db_context
from stt_service.providers import TranscriptionConfig, get_provider
from stt_service.services.rate_limiter import setup_default_limits
from stt_service.services.storage import storage_service
from stt_service.workers.celery_app import celery_app

logger = structlog.get_logger()
settings = get_settings()


def run_async(coro):
    """Run async coroutine in sync context."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(bind=True, max_retries=3)
def process_transcription_job(self, job_id: str) -> dict[str, Any]:
    """Main task to process a transcription job.

    This task:
    1. Downloads the audio file from S3
    2. Chunks it if necessary
    3. Processes each chunk with the STT provider
    4. Merges results and stores final transcript

    Args:
        job_id: The job ID to process

    Returns:
        Dict with job result summary
    """
    return run_async(_process_transcription_job(self, job_id))


async def _process_transcription_job(task, job_id: str) -> dict[str, Any]:
    """Async implementation of job processing."""
    setup_default_limits()

    async with get_db_context() as session:
        job_repo = JobRepository(session)
        chunk_repo = ChunkRepository(session)

        try:
            # Get job
            job = await job_repo.get_by_id(job_id, include_chunks=True)

            if job.status not in [JobStatus.UPLOADED, JobStatus.PROCESSING]:
                logger.warning("Job not in processable state", job_id=job_id, status=job.status)
                return {"job_id": job_id, "status": str(job.status)}

            # Update status to processing
            await job_repo.update_status(job_id, JobStatus.PROCESSING)

            # Debug: log job attributes
            logger.info(
                "Job loaded",
                job_id=job_id,
                job_config_type=type(job.config).__name__,
                job_config=job.config,
                job_provider=job.provider,
            )

            config = job.config or {}
            provider_name = job.provider or config.get("provider", "gemini")

            logger.info(
                "Starting transcription job",
                job_id=job_id,
                provider=provider_name,
                duration=job.duration_seconds,
            )

            # Download audio file
            with tempfile.TemporaryDirectory() as temp_dir:
                audio_path = os.path.join(temp_dir, "audio")
                await storage_service.download_file_to_path(
                    job.s3_original_key,
                    audio_path,
                )

                # Check if we need chunking
                chunker = AudioChunker()
                metadata = await chunker.get_audio_metadata(audio_path)

                if metadata.duration <= settings.chunking.max_chunk_duration:
                    # Single chunk processing
                    chunks = [
                        ChunkInfo(
                            index=0,
                            start_time=0,
                            end_time=metadata.duration,
                            duration=metadata.duration,
                            file_path=audio_path,
                        )
                    ]
                else:
                    # Multi-chunk processing
                    chunks = await chunker.chunk_audio(audio_path, temp_dir)

                # Create chunk records if not exists
                if not job.chunks:
                    chunks_data = [
                        {
                            "chunk_index": c.index,
                            "start_time": c.start_time,
                            "end_time": c.end_time,
                            "s3_chunk_key": None,  # We'll process locally
                        }
                        for c in chunks
                    ]
                    await chunk_repo.create_many(job_id, chunks_data)
                    await job_repo.update_chunk_counts(job_id, total_chunks=len(chunks))

                # Process chunks
                provider = get_provider(provider_name)
                transcription_config = _build_transcription_config(config)
                results = []

                for chunk in chunks:
                    chunk_record = await chunk_repo.get_by_job_and_index(job_id, chunk.index)
                    if chunk_record and chunk_record.status == ChunkStatus.COMPLETED:
                        # Skip already completed chunks
                        results.append(chunk_record.result)
                        continue

                    # Mark as processing
                    if chunk_record:
                        await chunk_repo.mark_processing(chunk_record.id)

                    # Transcribe chunk
                    try:
                        result = await _process_single_chunk(
                            provider,
                            chunk,
                            transcription_config,
                            provider_name,
                        )

                        # Store result
                        if chunk_record:
                            await chunk_repo.set_result(chunk_record.id, result)
                            await job_repo.increment_completed_chunks(job_id)

                        results.append(result)

                    except Exception as e:
                        logger.error(
                            "Chunk processing failed",
                            job_id=job_id,
                            chunk_index=chunk.index,
                            error=str(e),
                        )
                        if chunk_record:
                            await chunk_repo.update_status(
                                chunk_record.id,
                                ChunkStatus.FAILED,
                                error=str(e),
                            )
                        raise

                # Merge results
                merger = TranscriptMerger()
                final_transcript = merger.merge_transcripts(results, chunks)

                # Store final result
                result_key = storage_service.generate_result_key(job_id)
                await storage_service.upload_json(result_key, final_transcript)

                await job_repo.set_result(job_id, final_transcript, result_key)

                logger.info("Transcription job completed", job_id=job_id)

                # Send webhook if configured
                if job.webhook_url:
                    send_webhook.delay(job_id, job.webhook_url)

                return {
                    "job_id": job_id,
                    "status": "completed",
                    "chunks_processed": len(results),
                }

        except Exception as e:
            logger.error("Transcription job failed", job_id=job_id, error=str(e))
            await job_repo.update_status(job_id, JobStatus.FAILED, error_message=str(e))

            # Retry if possible
            if task.request.retries < task.max_retries:
                raise task.retry(exc=e, countdown=60 * (task.request.retries + 1))

            return {"job_id": job_id, "status": "failed", "error": str(e)}


async def _process_single_chunk(
    provider,
    chunk: ChunkInfo,
    config: TranscriptionConfig,
    provider_name: str,
) -> dict[str, Any]:
    """Process a single audio chunk with retry logic."""

    async def do_transcribe():
        with open(chunk.file_path, "rb") as f:
            audio_data = f.read()
        return await provider.transcribe(audio_data, config)

    result = await retry_with_backoff(
        do_transcribe,
        config=RetryConfig.from_settings(),
        provider=provider_name,
    )

    # Convert to dict for storage
    return {
        "text": result.text,
        "segments": [
            {
                "text": s.text,
                "start_time": s.start_time,
                "end_time": s.end_time,
                "speaker_id": s.speaker_id,
                "confidence": s.confidence,
            }
            for s in result.segments
        ],
        "language_detected": result.language_detected,
        "chunk_start_time": chunk.start_time,
        "chunk_end_time": chunk.end_time,
    }


def _build_transcription_config(config: dict | None) -> TranscriptionConfig:
    """Build TranscriptionConfig from job config dict."""
    config = config or {}
    context = config.get("context") or {}
    diarization = config.get("diarization") or {}
    output = config.get("output") or {}

    return TranscriptionConfig(
        language=config.get("language", "hy"),
        additional_languages=config.get("additional_languages", ["en", "ru"]),
        prompt=context.get("prompt"),
        custom_vocabulary=context.get("custom_vocabulary", []),
        domain=context.get("domain"),
        diarization_enabled=diarization.get("enabled", True),
        min_speakers=diarization.get("min_speakers"),
        max_speakers=diarization.get("max_speakers"),
        include_timestamps=output.get("include_timestamps", True),
        timestamp_granularity=output.get("timestamp_granularity", "segment"),
        include_confidence=output.get("include_confidence", False),
    )


@celery_app.task(bind=True, max_retries=3)
def process_chunk(
    self,
    job_id: str,
    chunk_index: int,
) -> dict[str, Any]:
    """Process a single chunk (for parallel processing).

    Args:
        job_id: Job ID
        chunk_index: Index of chunk to process

    Returns:
        Chunk result dict
    """
    return run_async(_process_chunk(self, job_id, chunk_index))


async def _process_chunk(task, job_id: str, chunk_index: int) -> dict[str, Any]:
    """Async implementation of single chunk processing."""
    setup_default_limits()

    async with get_db_context() as session:
        job_repo = JobRepository(session)
        chunk_repo = ChunkRepository(session)

        try:
            job = await job_repo.get_by_id(job_id)
            chunk = await chunk_repo.get_by_job_and_index(job_id, chunk_index)

            if not chunk:
                raise ValueError(f"Chunk not found: job={job_id}, index={chunk_index}")

            if chunk.status == ChunkStatus.COMPLETED:
                return chunk.result

            # Mark as processing
            await chunk_repo.mark_processing(chunk.id)

            # Download chunk audio
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                temp_path = f.name
                await storage_service.download_file_to_path(chunk.s3_chunk_key, temp_path)

            try:
                provider_name = job.provider or job.config.get("provider", "gemini")
                provider = get_provider(provider_name)
                config = _build_transcription_config(job.config)

                chunk_info = ChunkInfo(
                    index=chunk_index,
                    start_time=chunk.start_time,
                    end_time=chunk.end_time,
                    duration=chunk.end_time - chunk.start_time,
                    file_path=temp_path,
                )

                result = await _process_single_chunk(
                    provider, chunk_info, config, provider_name
                )

                await chunk_repo.set_result(chunk.id, result)
                await job_repo.increment_completed_chunks(job_id)

                return result

            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

        except Exception as e:
            logger.error(
                "Chunk processing failed",
                job_id=job_id,
                chunk_index=chunk_index,
                error=str(e),
            )

            if chunk:
                await chunk_repo.update_status(chunk.id, ChunkStatus.FAILED, error=str(e))

            if task.request.retries < task.max_retries:
                raise task.retry(exc=e, countdown=30 * (task.request.retries + 1))

            raise


@celery_app.task(bind=True, max_retries=5)
def send_webhook(self, job_id: str, webhook_url: str) -> dict[str, Any]:
    """Send webhook notification for completed job.

    Args:
        job_id: Job ID
        webhook_url: URL to POST result to

    Returns:
        Webhook delivery status
    """
    return run_async(_send_webhook(self, job_id, webhook_url))


async def _send_webhook(task, job_id: str, webhook_url: str) -> dict[str, Any]:
    """Async implementation of webhook sending."""
    async with get_db_context() as session:
        job_repo = JobRepository(session)

        try:
            job = await job_repo.get_by_id(job_id)

            payload = {
                "job_id": job_id,
                "status": str(job.status.value),
                "result": job.result,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            }

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    webhook_url,
                    json=payload,
                    timeout=30.0,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()

            await job_repo.mark_webhook_sent(job_id)

            logger.info("Webhook sent", job_id=job_id, webhook_url=webhook_url)
            return {"status": "sent", "status_code": response.status_code}

        except Exception as e:
            logger.error(
                "Webhook failed",
                job_id=job_id,
                webhook_url=webhook_url,
                error=str(e),
            )

            if task.request.retries < task.max_retries:
                raise task.retry(exc=e, countdown=60 * (task.request.retries + 1))

            return {"status": "failed", "error": str(e)}


@celery_app.task
def retry_failed_job(job_id: str) -> dict[str, Any]:
    """Retry a failed job from the last successful checkpoint.

    Args:
        job_id: Job ID to retry

    Returns:
        Retry status
    """
    return run_async(_retry_failed_job(job_id))


async def _retry_failed_job(job_id: str) -> dict[str, Any]:
    """Async implementation of job retry."""
    async with get_db_context() as session:
        job_repo = JobRepository(session)
        chunk_repo = ChunkRepository(session)

        job = await job_repo.get_by_id(job_id)

        if job.status != JobStatus.FAILED:
            return {"status": "skipped", "reason": f"Job status is {job.status}, not FAILED"}

        # Reset failed chunks to pending
        reset_count = await chunk_repo.reset_failed_chunks(job_id)

        # Update job status back to processing
        await job_repo.update_status(job_id, JobStatus.PROCESSING)

        logger.info(
            "Retrying failed job",
            job_id=job_id,
            reset_chunks=reset_count,
        )

        # Trigger reprocessing
        process_transcription_job.delay(job_id)

        return {"status": "retrying", "reset_chunks": reset_count}
