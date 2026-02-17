"""Custom exceptions for STT Service."""

from typing import Any


class STTServiceError(Exception):
    """Base exception for STT Service."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        self.message = message
        self.details = details or {}
        super().__init__(message)


class ValidationError(STTServiceError):
    """Validation error."""

    pass


class AuthenticationError(STTServiceError):
    """Authentication failure."""

    pass


class ProviderError(STTServiceError):
    """Error from STT provider."""

    def __init__(
        self,
        message: str,
        provider: str,
        details: dict[str, Any] | None = None,
        retryable: bool = True,
    ) -> None:
        super().__init__(message, details)
        self.provider = provider
        self.retryable = retryable


class RateLimitError(ProviderError):
    """Rate limit exceeded."""

    def __init__(
        self,
        message: str,
        provider: str,
        retry_after: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, provider, details, retryable=True)
        self.retry_after = retry_after


class TranscriptionError(STTServiceError):
    """Error during transcription."""

    pass


class ChunkingError(STTServiceError):
    """Error during audio chunking."""

    pass


class StorageError(STTServiceError):
    """Error with S3/storage operations."""

    pass


class JobNotFoundError(STTServiceError):
    """Job not found."""

    pass


class ProjectNotFoundError(STTServiceError):
    """Project not found."""

    pass


class JobCancelledError(STTServiceError):
    """Job was cancelled or deleted."""

    pass


class JobAlreadyExistsError(STTServiceError):
    """Job already exists."""

    pass


class InvalidAudioFormatError(ValidationError):
    """Invalid audio format."""

    def __init__(self, format: str, supported_formats: list[str]) -> None:
        message = f"Unsupported audio format: {format}. Supported: {', '.join(supported_formats)}"
        super().__init__(message, {"format": format, "supported": supported_formats})


class FileTooLargeError(ValidationError):
    """File exceeds size limit."""

    def __init__(self, size: int, max_size: int) -> None:
        message = f"File size {size} bytes exceeds maximum {max_size} bytes"
        super().__init__(message, {"size": size, "max_size": max_size})
