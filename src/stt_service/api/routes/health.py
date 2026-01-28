"""Health check endpoints."""

import redis.asyncio as redis
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from stt_service.api.schemas.job import (
    HealthResponse,
    ProviderStatus,
    ProvidersStatusResponse,
    ReadinessResponse,
)
from stt_service.config import Settings, get_settings
from stt_service.db.session import get_db_session
from stt_service.services.storage import StorageService, storage_service

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse)
async def health_check(settings: Settings = Depends(get_settings)) -> HealthResponse:
    """Basic health check endpoint."""
    return HealthResponse(
        status="healthy",
        version=settings.app_version,
    )


@router.get("/health/ready", response_model=ReadinessResponse)
async def readiness_check(
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db_session),
) -> ReadinessResponse:
    """Readiness probe checking all dependencies."""
    # Check database
    db_status = "healthy"
    try:
        await db.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"unhealthy: {str(e)[:100]}"

    # Check Redis
    redis_status = "healthy"
    try:
        redis_client = redis.from_url(settings.redis.url)
        await redis_client.ping()
        await redis_client.close()
    except Exception as e:
        redis_status = f"unhealthy: {str(e)[:100]}"

    # Check S3/Storage
    storage_status = "healthy"
    try:
        storage: StorageService = storage_service
        await storage.ensure_bucket_exists()
    except Exception as e:
        storage_status = f"unhealthy: {str(e)[:100]}"

    # Overall status
    all_healthy = all(
        s == "healthy" for s in [db_status, redis_status, storage_status]
    )

    return ReadinessResponse(
        status="ready" if all_healthy else "degraded",
        database=db_status,
        redis=redis_status,
        storage=storage_status,
    )


@router.get("/health/providers", response_model=ProvidersStatusResponse)
async def providers_status(
    settings: Settings = Depends(get_settings),
) -> ProvidersStatusResponse:
    """Check availability of STT providers."""
    providers = []

    # Gemini
    providers.append(
        ProviderStatus(
            name="gemini",
            configured=bool(settings.providers.gemini_api_key),
            available=bool(settings.providers.gemini_api_key),
            error=None if settings.providers.gemini_api_key else "API key not configured",
        )
    )

    # ElevenLabs
    providers.append(
        ProviderStatus(
            name="elevenlabs",
            configured=bool(settings.providers.elevenlabs_api_key),
            available=bool(settings.providers.elevenlabs_api_key),
            error=None
            if settings.providers.elevenlabs_api_key
            else "API key not configured",
        )
    )

    # OpenAI Whisper
    providers.append(
        ProviderStatus(
            name="whisper",
            configured=bool(settings.providers.openai_api_key),
            available=bool(settings.providers.openai_api_key),
            error=None if settings.providers.openai_api_key else "API key not configured",
        )
    )

    # AssemblyAI
    providers.append(
        ProviderStatus(
            name="assemblyai",
            configured=bool(settings.providers.assemblyai_api_key),
            available=bool(settings.providers.assemblyai_api_key),
            error=None
            if settings.providers.assemblyai_api_key
            else "API key not configured",
        )
    )

    # Deepgram
    providers.append(
        ProviderStatus(
            name="deepgram",
            configured=bool(settings.providers.deepgram_api_key),
            available=bool(settings.providers.deepgram_api_key),
            error=None if settings.providers.deepgram_api_key else "API key not configured",
        )
    )

    # HiSpeech
    providers.append(
        ProviderStatus(
            name="hispeech",
            configured=bool(settings.providers.hispeech_api_key),
            available=bool(settings.providers.hispeech_api_key),
            error=None if settings.providers.hispeech_api_key else "API key not configured",
        )
    )

    return ProvidersStatusResponse(providers=providers)
