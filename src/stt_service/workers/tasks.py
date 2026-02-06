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
from stt_service.utils.logging_config import job_logging_context
from stt_service.providers import TranscriptionConfig, get_provider
from stt_service.services.rate_limiter import setup_default_limits
from stt_service.services.storage import storage_service
from stt_service.workers.celery_app import celery_app
from stt_service.utils.exceptions import JobCancelledError, JobNotFoundError, ProviderError

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
    
    # Bind job_id to all logs in this task context
    structlog.contextvars.bind_contextvars(job_id=job_id)

    # Use separate log file for this job
    with job_logging_context(job_id):
        logger.info("="*50 + "\n>>> STAGE: WORKER STARTED (Received from Redis) <<<\n" + "="*50)

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
            await session.commit()  # Commit to show "processing" status in UI

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
                logger.info("="*50 + "\n>>> STAGE: AUDIO CHUNKER <<<\n" + "="*50)
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
                    await session.commit()  # Commit to make chunks visible in UI

                # Process chunks
                logger.info("="*50 + f"\n>>> STAGE: PROVIDER PROCESSING ({provider_name.upper()}) <<<\n" + "="*50)
                provider = get_provider(provider_name)
                base_config = _build_transcription_config(config)
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
                        await session.commit()  # Commit to show "processing" status

                    # Build chunk-specific config with context from previous chunks
                    context_text, known_speakers = _extract_context_from_results(
                        results,
                        num_segments=settings.chunking.context_segments,
                    )

                    # Create config with context for this chunk
                    chunk_config = TranscriptionConfig(
                        language=base_config.language,
                        additional_languages=base_config.additional_languages,
                        prompt=base_config.prompt,
                        custom_vocabulary=base_config.custom_vocabulary,
                        domain=base_config.domain,
                        previous_transcript_context=context_text if chunk.index > 0 else None,
                        previous_speakers=known_speakers if chunk.index > 0 else [],
                        chunk_index=chunk.index,
                        diarization_enabled=base_config.diarization_enabled,
                        min_speakers=base_config.min_speakers,
                        max_speakers=base_config.max_speakers,
                        include_timestamps=base_config.include_timestamps,
                        timestamp_granularity=base_config.timestamp_granularity,
                        include_confidence=base_config.include_confidence,
                        audio_duration=chunk.duration,
                    )

                    if chunk.index > 0 and context_text:
                        logger.info(
                            "Passing context to chunk",
                            chunk_index=chunk.index,
                            context_length=len(context_text),
                            known_speakers=known_speakers,
                        )

                    # Transcribe chunk
                    try:
                        # Define retry callback to check for cancellation
                        async def on_retry_check(attempt, exc, delay):
                            job = await job_repo.get_by_id_or_none(job_id)
                            if not job:
                                raise JobCancelledError(f"Job {job_id} not found (deleted)")
                            if job.status not in [JobStatus.UPLOADED, JobStatus.PROCESSING]:
                                raise JobCancelledError(f"Job {job_id} status is {job.status}")

                        result = await _process_single_chunk(
                            provider,
                            chunk,
                            chunk_config,
                            provider_name,
                            on_retry=on_retry_check,
                        )

                        # Store result
                        if chunk_record:
                            await chunk_repo.set_result(chunk_record.id, result)
                            await job_repo.increment_completed_chunks(job_id)
                            await session.commit()  # Commit progress after each chunk

                        results.append(result)

                        # Save individual chunk JSON for debugging
                        import json
                        log_dir = f"logs/jobs/{job_id}"
                        os.makedirs(log_dir, exist_ok=True)
                        chunk_debug_path = os.path.join(log_dir, f"chunk-{chunk.index:04d}.json")
                        try:
                            with open(chunk_debug_path, "w", encoding="utf-8") as f:
                                json.dump(result, f, indent=2, ensure_ascii=False)
                            logger.info("Saved chunk transcript", path=chunk_debug_path)
                        except Exception as e:
                            logger.error("Failed to save chunk transcript", error=str(e))

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
                            await session.commit()  # Commit error status
                        raise

                # Save intermediate combined JSON for debugging
                import json
                log_dir = f"logs/jobs/{job_id}"
                os.makedirs(log_dir, exist_ok=True)
                combined_debug_path = os.path.join(log_dir, "combined_transcript.json")
                try:
                    with open(combined_debug_path, "w", encoding="utf-8") as f:
                        json.dump(results, f, indent=2, ensure_ascii=False)
                    logger.info("Saved intermediate combined transcript", path=combined_debug_path)
                except Exception as e:
                    logger.error("Failed to save intermediate transcript", error=str(e))

                # Merge results
                logger.info("="*50 + "\n>>> STAGE: TRANSCRIPT MERGER <<<\n" + "="*50)
                merger = TranscriptMerger()
                final_transcript = merger.merge_transcripts(results, chunks)

                # Store final result
                logger.info("="*50 + "\n>>> STAGE: DB & STORAGE PERSISTENCE <<<\n" + "="*50)
                result_key = storage_service.generate_result_key(job_id)
                await storage_service.upload_json(result_key, final_transcript)

                await job_repo.set_result(job_id, final_transcript, result_key)
                await session.commit()  # Explicitly commit the final state

                logger.info("Transcription job completed", job_id=job_id)

                # Send webhook if configured
                if job.webhook_url:
                    send_webhook.delay(job_id, job.webhook_url)

                return {
                    "job_id": job_id,
                    "status": "completed",
                    "chunks_processed": len(results),
                }

        except (JobCancelledError, JobNotFoundError) as e:
            logger.warning("Job cancelled or deleted, aborting task", job_id=job_id, reason=str(e))
            return {"job_id": job_id, "status": "cancelled", "reason": str(e)}

        except Exception as e:
            logger.error("Transcription job failed", job_id=job_id, error=str(e))
            await job_repo.update_status(job_id, JobStatus.FAILED, error_message=str(e))

            # Check for non-retryable errors
            if isinstance(e, ProviderError) and not e.retryable:
                logger.error("Non-retryable error encountered, failing job immediately", job_id=job_id, error=str(e))
                return {"job_id": job_id, "status": "failed", "error": str(e), "retryable": False}

            # Retry if possible
            if task.request.retries < task.max_retries:
                raise task.retry(exc=e, countdown=60 * (task.request.retries + 1))

            return {"job_id": job_id, "status": "failed", "error": str(e)}


async def _process_single_chunk(
    provider,
    chunk: ChunkInfo,
    config: TranscriptionConfig,
    provider_name: str,
    on_retry: Any = None,
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
        on_retry=on_retry,
    )

    # Convert to dict for storage
    return {
        "text": result.text,
        "full_text": result.text,  # Alias for debug/intermediate JSON
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


def _extract_context_from_results(
    results: list[dict],
    num_segments: int = 3,
) -> tuple[str, list[str]]:
    """Extract context from previous chunk results for continuity.

    Args:
        results: List of previous chunk results
        num_segments: Number of segments to include in context

    Returns:
        Tuple of (context_text, speaker_ids)
    """
    if not results:
        return "", []

    # Collect all segments from all results
    all_segments = []
    all_speakers = set()

    for result in results:
        segments = result.get("segments", [])
        for seg in segments:
            all_segments.append(seg)
            speaker = seg.get("speaker_id") or seg.get("speaker")
            if speaker:
                all_speakers.add(speaker)

    if not all_segments:
        logger.debug("No segments found in previous results for context")
        return "", list(all_speakers)

    # Take last N segments for context
    context_segments = all_segments[-num_segments:]
    logger.debug(
        "Extracting context", 
        total_segments_available=len(all_segments), 
        segments_used=len(context_segments),
        speakers_found=list(all_speakers)
    )

    # Build context text
    context_lines = []
    for seg in context_segments:
        speaker = seg.get("speaker_id") or seg.get("speaker", "SPEAKER_00")
        text = seg.get("text", "").strip()
        if text:
            context_lines.append(f"{speaker}: {text}")

    return "\n".join(context_lines), list(all_speakers)


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
    
    # Bind context for chunk logs
    structlog.contextvars.bind_contextvars(job_id=job_id, chunk_index=chunk_index)

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
                
                # inject duration into config
                config.audio_duration = chunk_info.duration

                # Define retry callback
                async def on_retry_check(attempt, exc, delay):
                    job_check = await job_repo.get_by_id_or_none(job_id)
                    if not job_check:
                        raise JobCancelledError(f"Job {job_id} not found (deleted)")
                    
                    # Also check chunk status?
                    chunk_check = await chunk_repo.get_by_job_and_index(job_id, chunk_index)
                    if not chunk_check or chunk_check.status == ChunkStatus.FAILED:
                         # If manually marked failed/deleted
                         raise JobCancelledError(f"Chunk {chunk_index} cancelled")

                result = await _process_single_chunk(
                    provider, chunk_info, config, provider_name, on_retry=on_retry_check
                )

                await chunk_repo.set_result(chunk.id, result)
                await job_repo.increment_completed_chunks(job_id)

                return result

            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

        except (JobCancelledError, JobNotFoundError) as e:
             logger.warning("Chunk cancelled or deleted, aborting", job_id=job_id, chunk_index=chunk_index)
             # Do NOT raise retry, just exit
             return {"status": "cancelled", "reason": str(e)}

        except Exception as e:
            logger.error(
                "Chunk processing failed",
                job_id=job_id,
                chunk_index=chunk_index,
                error=str(e),
            )

            if chunk:
                await chunk_repo.update_status(chunk.id, ChunkStatus.FAILED, error=str(e))

            # Check for non-retryable errors
            if isinstance(e, ProviderError) and not e.retryable:
                 logger.error("Non-retryable error encountered, failing chunk immediately", job_id=job_id, chunk_index=chunk_index, error=str(e))
                 # We do NOT raise retry here. We raise the exception to propagate failure or just return failed status
                 # If we raise, the caller (if task chain) handles it.
                 # Given this is a task, raising without retry marks task as FAILED.
                 raise e

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
