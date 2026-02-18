"""Database session management."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from stt_service.config import get_settings

settings = get_settings()

# Create async engine for FastAPI (single event loop)
engine: AsyncEngine = create_async_engine(
    settings.database.url,
    pool_size=settings.database.pool_size,
    max_overflow=settings.database.max_overflow,
    echo=settings.debug,
)

# Create async session factory
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


def _create_worker_engine() -> AsyncEngine:
    """Create a fresh engine for worker tasks (separate event loop)."""
    return create_async_engine(
        settings.database.url,
        pool_size=5,
        max_overflow=10,
        echo=settings.debug,
    )


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting database sessions."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for database sessions (for Celery workers).

    Creates a fresh engine for each context to avoid event loop conflicts.
    """
    worker_engine = _create_worker_engine()
    worker_session_factory = async_sessionmaker(
        worker_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    async with worker_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    await worker_engine.dispose()


async def init_db() -> None:
    """Initialize database (create tables if needed)."""
    from sqlalchemy import text

    from stt_service.db.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Add columns that may be missing from older schemas
        await conn.execute(text(
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS error_code VARCHAR(50)"
        ))
        await conn.execute(text(
            "ALTER TABLE projects ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE SET NULL"
        ))

    # Seed default users if they don't exist
    from stt_service.db.repositories.user import UserRepository
    from stt_service.db.models import UserRole
    import structlog
    logger = structlog.get_logger()

    async with async_session_factory() as session:
        repo = UserRepository(session)

        # Create default admin
        admin = await repo.get_by_username("admin")
        if not admin:
            admin = await repo.create(
                username="admin",
                password="admin",
                role=UserRole.ADMIN,
                display_name="Administrator",
            )
            logger.info("Created default admin user", user_id=admin.id)

        # Create default user
        default_user = await repo.get_by_username("user")
        if not default_user:
            default_user = await repo.create(
                username="user",
                password="user",
                role=UserRole.USER,
                display_name="Default User",
            )
            logger.info("Created default user", user_id=default_user.id)

            # Assign orphan projects to default user
            from sqlalchemy import text as sql_text
            await session.execute(
                sql_text("UPDATE projects SET user_id = :uid WHERE user_id IS NULL"),
                {"uid": default_user.id},
            )
            logger.info("Assigned orphan projects to default user")

        await session.commit()


async def close_db() -> None:
    """Close database connections."""
    await engine.dispose()
