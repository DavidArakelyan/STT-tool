"""Classify provider exceptions into user-friendly error codes."""

from stt_service.utils.exceptions import ProviderError, RateLimitError


# Error codes returned to users
ERROR_RATE_LIMITED = "rate_limited"
ERROR_TIMEOUT = "timeout"
ERROR_INVALID_AUDIO = "invalid_audio"
ERROR_AUTH = "auth_error"
ERROR_PROVIDER_UNAVAILABLE = "provider_unavailable"
ERROR_QUOTA_EXCEEDED = "quota_exceeded"
ERROR_UNKNOWN = "unknown"

# Patterns matched against the lowercase exception string
_TIMEOUT_PATTERNS = ("timeout", "timed out", "deadline exceeded", "read timed out")
_AUTH_PATTERNS = ("401", "403", "unauthorized", "forbidden", "invalid api key", "permission denied")
_AUDIO_PATTERNS = ("invalid audio", "unsupported format", "corrupt", "could not decode", "bad request")
_UNAVAILABLE_PATTERNS = ("503", "502", "service unavailable", "bad gateway", "connection refused", "connection reset")
_QUOTA_PATTERNS = ("quota", "billing", "payment required", "402")


def classify_error(exc: Exception) -> tuple[str, str]:
    """Map an exception to (error_code, user_friendly_message).

    Returns:
        Tuple of (error_code, message) where error_code is one of the
        ERROR_* constants and message is a short human-readable explanation.
    """
    # Already classified by our exception hierarchy
    if isinstance(exc, RateLimitError):
        return ERROR_RATE_LIMITED, (
            "The transcription provider is temporarily rate-limiting requests. "
            "Please try again in a few minutes."
        )

    if isinstance(exc, ProviderError) and not exc.retryable:
        msg_lower = str(exc).lower()
        if any(p in msg_lower for p in _AUTH_PATTERNS):
            return ERROR_AUTH, (
                "Authentication with the transcription provider failed. "
                "Please check provider API key configuration."
            )
        if any(p in msg_lower for p in _AUDIO_PATTERNS):
            return ERROR_INVALID_AUDIO, (
                "The audio file could not be processed by the provider. "
                "It may be corrupted or in an unsupported format."
            )

    # Fall back to string matching on any exception
    msg_lower = str(exc).lower()

    if any(p in msg_lower for p in _TIMEOUT_PATTERNS):
        return ERROR_TIMEOUT, (
            "The transcription request timed out. "
            "This can happen with very long audio files. Please try again."
        )

    if "429" in msg_lower or "resource exhausted" in msg_lower or "resourceexhausted" in msg_lower:
        return ERROR_RATE_LIMITED, (
            "The transcription provider is temporarily rate-limiting requests. "
            "Please try again in a few minutes."
        )

    if any(p in msg_lower for p in _QUOTA_PATTERNS):
        return ERROR_QUOTA_EXCEEDED, (
            "The provider API quota has been exceeded. "
            "Please contact the administrator."
        )

    if any(p in msg_lower for p in _AUTH_PATTERNS):
        return ERROR_AUTH, (
            "Authentication with the transcription provider failed. "
            "Please check provider API key configuration."
        )

    if any(p in msg_lower for p in _AUDIO_PATTERNS):
        return ERROR_INVALID_AUDIO, (
            "The audio file could not be processed by the provider. "
            "It may be corrupted or in an unsupported format."
        )

    if any(p in msg_lower for p in _UNAVAILABLE_PATTERNS):
        return ERROR_PROVIDER_UNAVAILABLE, (
            "The transcription provider is currently unavailable. "
            "Please try again later."
        )

    return ERROR_UNKNOWN, f"Transcription failed: {exc}"
