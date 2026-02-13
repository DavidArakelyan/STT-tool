"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from stt_service.api.routes import health, jobs, transcription, settings as settings_api
from stt_service.config import get_settings
from stt_service.db.session import close_db, init_db
from stt_service.services.storage import storage_service
from stt_service.utils.logging_config import configure_logging

# Configure logging before doing anything else
configure_logging()
from stt_service.utils.exceptions import (
    AuthenticationError,
    JobNotFoundError,
    ProviderError,
    STTServiceError,
    ValidationError,
)

settings = get_settings()
logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler."""
    # Startup
    logger.info("Starting STT Service", version=settings.app_version)

    # Initialize database
    if settings.environment == "development":
        await init_db()
        logger.info("Database initialized")

    # Ensure S3 bucket exists
    try:
        await storage_service.ensure_bucket_exists()
        logger.info("Storage bucket verified")
    except Exception as e:
        logger.warning("Storage bucket check failed", error=str(e))

    yield

    # Shutdown
    logger.info("Shutting down STT Service")
    await close_db()


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Speech-to-Text service with multi-provider support, speaker diarization, and Armenian language optimization",
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        openapi_url="/openapi.json" if settings.debug else None,
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["X-API-Key", "Content-Type"],
    )

    # Exception handlers
    @app.exception_handler(ValidationError)
    async def validation_error_handler(
        request: Request, exc: ValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "error": "Validation Error",
                "detail": exc.message,
                "code": "VALIDATION_ERROR",
            },
        )

    @app.exception_handler(AuthenticationError)
    async def auth_error_handler(
        request: Request, exc: AuthenticationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={
                "error": "Authentication Error",
                "detail": exc.message,
                "code": "AUTH_ERROR",
            },
        )

    @app.exception_handler(JobNotFoundError)
    async def job_not_found_handler(
        request: Request, exc: JobNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={
                "error": "Not Found",
                "detail": exc.message,
                "code": "JOB_NOT_FOUND",
            },
        )

    @app.exception_handler(ProviderError)
    async def provider_error_handler(
        request: Request, exc: ProviderError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={
                "error": "Provider Error",
                "detail": exc.message,
                "code": "PROVIDER_ERROR",
                "provider": exc.provider,
            },
        )

    @app.exception_handler(STTServiceError)
    async def stt_error_handler(
        request: Request, exc: STTServiceError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "error": "Service Error",
                "detail": exc.message,
                "code": "SERVICE_ERROR",
            },
        )

    # Include routers
    app.include_router(health.router)

    # API v1 routers
    app.include_router(
        transcription.router,
        prefix=settings.api_prefix,
    )
    app.include_router(
        jobs.router,
        prefix=settings.api_prefix,
    )
    app.include_router(
        settings_api.router,
        prefix=settings.api_prefix,
    )

    # Mount frontend static files
    import os
    frontend_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "frontend")
    if os.path.exists(frontend_path):
        app.mount("/static", StaticFiles(directory=frontend_path), name="static")

        @app.get("/")
        async def serve_frontend() -> FileResponse:
            """Serve the frontend application."""
            return FileResponse(os.path.join(frontend_path, "index.html"))

    return app


# Create app instance
app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "stt_service.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
