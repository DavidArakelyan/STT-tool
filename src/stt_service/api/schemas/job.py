"""Job-related API schemas."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str


class ReadinessResponse(BaseModel):
    """Readiness check response with component status."""

    status: str
    database: str
    redis: str
    storage: str


class ProviderStatus(BaseModel):
    """Status of a single STT provider."""

    name: str
    available: bool
    configured: bool
    error: str | None = None


class ProvidersStatusResponse(BaseModel):
    """Status of all STT providers."""

    providers: list[ProviderStatus]


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str
    detail: str | None = None
    code: str | None = None


class MessageResponse(BaseModel):
    """Simple message response."""

    message: str
