"""Transcription API endpoints."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from stt_service.api.dependencies import APIKey, AppSettings, ChunkRepo, JobRepo, RateLimit, Storage
from stt_service.api.schemas.transcription import (
    JobStatus,
    TranscriptionRequest,
    TranscriptionSubmitResponse,
    TranscriptionUrlRequest,
)
from stt_service.core.orchestrator import JobOrchestrator
from stt_service.utils.exceptions import FileTooLargeError, InvalidAudioFormatError
from stt_service.utils.file_validation import is_valid_media_file

router = APIRouter(prefix="/transcribe", tags=["Transcription"])


def get_orchestrator(
    job_repo: JobRepo,
    chunk_repo: ChunkRepo,
    storage: Storage,
) -> JobOrchestrator:
    """Get job orchestrator instance."""
    return JobOrchestrator(job_repo, chunk_repo, storage)


@router.post("", response_model=TranscriptionSubmitResponse)
async def submit_transcription(
    settings: AppSettings,
    _api_key: APIKey,
    _rate_limit: RateLimit,
    audio: UploadFile = File(..., description="Audio file to transcribe"),
    config: str = Form(
        default="{}",
        description="JSON configuration for transcription (TranscriptionRequest schema)",
    ),
    orchestrator: JobOrchestrator = Depends(get_orchestrator),
) -> TranscriptionSubmitResponse:
    """Submit a new transcription job with audio file upload.

    Upload an audio file along with optional JSON configuration.
    The job will be queued for processing and you can poll the status.

    Supported formats: mp3, wav, m4a, flac, ogg, webm, aac, wma, opus
    """
    import json

    # Validate file format
    filename = audio.filename or "audio.mp3"
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if extension not in settings.supported_media_formats:
        raise InvalidAudioFormatError(extension, settings.supported_media_formats)

    # Read and validate file size
    audio_data = await audio.read()
    if len(audio_data) > settings.max_upload_size:
        raise FileTooLargeError(len(audio_data), settings.max_upload_size)

    # Verify file content matches a known audio/video format
    if not is_valid_media_file(audio_data):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File content does not match any supported audio/video format. "
            "The file may be corrupted or not a real media file.",
        )

    # Parse configuration
    try:
        config_dict = json.loads(config) if config else {}
        request = TranscriptionRequest(**config_dict)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON configuration: {e}",
        )

    # Create job
    job_id = await orchestrator.create_job(
        config=request.model_dump(),
        provider=request.provider.value,
        filename=filename,
        file_size=len(audio_data),
        webhook_url=request.webhook_url,
    )

    # Upload audio
    await orchestrator.upload_audio(
        job_id=job_id,
        audio_data=audio_data,
        filename=filename,
        content_type=audio.content_type,
    )

    # Submit for processing
    await orchestrator.submit_job(job_id)

    return TranscriptionSubmitResponse(
        job_id=job_id,
        status=JobStatus.PROCESSING,
        message="Transcription job submitted successfully",
    )


@router.post("/url", response_model=TranscriptionSubmitResponse)
async def submit_transcription_url(
    request: TranscriptionUrlRequest,
    settings: AppSettings,
    _api_key: APIKey,
    _rate_limit: RateLimit,
    orchestrator: JobOrchestrator = Depends(get_orchestrator),
) -> TranscriptionSubmitResponse:
    """Submit a transcription job with audio URL.

    Provide a publicly accessible URL to the audio file.
    The service will download and process the audio.
    """
    import httpx

    # Download audio from URL
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                request.audio_url,
                follow_redirects=True,
                timeout=60.0,
            )
            response.raise_for_status()
            audio_data = response.content
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to download audio from URL: {e}",
        )

    # Validate size
    if len(audio_data) > settings.max_upload_size:
        raise FileTooLargeError(len(audio_data), settings.max_upload_size)

    # Verify file content matches a known audio/video format
    if not is_valid_media_file(audio_data):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Downloaded content does not match any supported audio/video format.",
        )

    # Extract filename from URL
    filename = request.audio_url.split("/")[-1].split("?")[0] or "audio.mp3"
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if extension not in settings.supported_media_formats:
        raise InvalidAudioFormatError(extension, settings.supported_media_formats)

    # Create job
    job_id = await orchestrator.create_job(
        config=request.model_dump(exclude={"audio_url"}),
        provider=request.provider.value,
        filename=filename,
        file_size=len(audio_data),
        webhook_url=request.webhook_url,
    )

    # Upload audio
    await orchestrator.upload_audio(
        job_id=job_id,
        audio_data=audio_data,
        filename=filename,
    )

    # Submit for processing
    await orchestrator.submit_job(job_id)

    return TranscriptionSubmitResponse(
        job_id=job_id,
        status=JobStatus.PROCESSING,
        message="Transcription job submitted successfully",
    )
