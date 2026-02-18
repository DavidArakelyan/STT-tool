"""FastAPI dependencies."""

from collections.abc import AsyncGenerator
from typing import Annotated

import redis.asyncio as aioredis
import structlog
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from stt_service.config import Settings, get_settings
from stt_service.db.repositories.chunk import ChunkRepository
from stt_service.db.repositories.job import JobRepository
from stt_service.db.repositories.project import ProjectRepository
from stt_service.db.repositories.user import UserRepository
from stt_service.db.models import User, UserRole
from stt_service.db.session import get_db_session
from stt_service.services.storage import StorageService, storage_service
from stt_service.api.routes.auth import decode_token

logger = structlog.get_logger()


def get_storage() -> StorageService:
    """Get storage service instance."""
    return storage_service


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_db_session),
) -> User:
    """Extract and validate the current user from Bearer token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Please log in.",
        )

    token = authorization.removeprefix("Bearer ").strip()
    user_id = decode_token(token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
        )

    repo = UserRepository(session)
    try:
        user = await repo.get_by_id(user_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is deactivated.",
        )

    return user


async def require_admin(
    user: User = Depends(get_current_user),
) -> User:
    """Require admin role."""
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return user


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


async def get_project_repository(
    session: AsyncSession = Depends(get_db_session),
) -> AsyncGenerator[ProjectRepository, None]:
    """Get project repository with database session."""
    yield ProjectRepository(session)


async def get_user_repository(
    session: AsyncSession = Depends(get_db_session),
) -> AsyncGenerator[UserRepository, None]:
    """Get user repository with database session."""
    yield UserRepository(session)


async def check_rate_limit(
    request: Request,
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> None:
    """Enforce per-user rate limit on transcription submissions."""
    rpm = settings.rate_limit_rpm
    if rpm <= 0:
        return  # disabled

    key = f"ratelimit:{user.id}"
    try:
        r = aioredis.from_url(settings.redis.url)
        try:
            count = await r.incr(key)
            if count == 1:
                await r.expire(key, 60)  # 1 minute window
            if count > rpm:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded. Maximum {rpm} requests per minute.",
                )
        finally:
            await r.aclose()
    except HTTPException:
        raise
    except Exception as e:
        # If Redis is down, allow the request (fail-open)
        logger.warning("Rate limit check failed — allowing request", error=str(e))


# Type aliases for cleaner dependency injection
DBSession = Annotated[AsyncSession, Depends(get_db_session)]
JobRepo = Annotated[JobRepository, Depends(get_job_repository)]
ChunkRepo = Annotated[ChunkRepository, Depends(get_chunk_repository)]
ProjectRepo = Annotated[ProjectRepository, Depends(get_project_repository)]
UserRepo = Annotated[UserRepository, Depends(get_user_repository)]
Storage = Annotated[StorageService, Depends(get_storage)]
CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(require_admin)]
AppSettings = Annotated[Settings, Depends(get_settings)]
RateLimit = Annotated[None, Depends(check_rate_limit)]
