"""FastAPI dependencies."""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from stt_service.config import Settings, get_settings
from stt_service.db.repositories.chunk import ChunkRepository
from stt_service.db.repositories.job import JobRepository
from stt_service.db.session import get_db_session
from stt_service.services.storage import StorageService, storage_service


def get_storage() -> StorageService:
    """Get storage service instance."""
    return storage_service


async def verify_api_key(
    x_api_key: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
) -> str:
    """Verify API key from header."""
    api_keys = settings.api_keys_list
    if not api_keys:
        # No API keys configured = no auth required (development mode)
        return "anonymous"

    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Provide X-API-Key header.",
        )

    if x_api_key not in api_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )

    return x_api_key


async def get_job_repository(
    session: AsyncSession = Depends(get_db_session),
) -> AsyncGenerator[JobRepository, None]:
    """Get job repository with database session."""
    yield JobRepository(session)


async def get_chunk_repository(
    session: AsyncSession = Depends(get_db_session),
) -> AsyncGenerator[ChunkRepository, None]:
    """Get chunk repository with database session."""
    yield ChunkRepository(session)


# Type aliases for cleaner dependency injection
DBSession = Annotated[AsyncSession, Depends(get_db_session)]
JobRepo = Annotated[JobRepository, Depends(get_job_repository)]
ChunkRepo = Annotated[ChunkRepository, Depends(get_chunk_repository)]
Storage = Annotated[StorageService, Depends(get_storage)]
APIKey = Annotated[str, Depends(verify_api_key)]
AppSettings = Annotated[Settings, Depends(get_settings)]
