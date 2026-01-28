"""Token bucket rate limiter for STT providers."""

import asyncio
import time
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger()


@dataclass
class RateLimitState:
    """State for a single rate limit bucket."""

    tokens: float
    last_update: float
    max_tokens: float
    refill_rate: float  # tokens per second
    adaptive_factor: float = 1.0  # Multiplier for backoff

    def get_available_tokens(self, now: float) -> float:
        """Calculate available tokens at given time."""
        elapsed = now - self.last_update
        new_tokens = elapsed * self.refill_rate * self.adaptive_factor
        return min(self.max_tokens, self.tokens + new_tokens)


class RateLimiter:
    """Token bucket rate limiter with adaptive backoff.

    Features:
    - Per-provider rate limiting
    - Adaptive rate based on 429 responses
    - Pre-emptive throttling
    - Async-safe with locks
    """

    def __init__(self) -> None:
        self._buckets: dict[str, RateLimitState] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    async def _get_lock(self, provider: str) -> asyncio.Lock:
        """Get or create a lock for a provider."""
        if provider not in self._locks:
            async with self._global_lock:
                if provider not in self._locks:
                    self._locks[provider] = asyncio.Lock()
        return self._locks[provider]

    def configure_provider(
        self,
        provider: str,
        requests_per_minute: int,
        burst_size: int | None = None,
    ) -> None:
        """Configure rate limit for a provider.

        Args:
            provider: Provider name
            requests_per_minute: Maximum requests per minute
            burst_size: Maximum burst size (defaults to RPM / 6)
        """
        if burst_size is None:
            burst_size = max(1, requests_per_minute // 6)  # ~10 seconds worth

        refill_rate = requests_per_minute / 60.0  # tokens per second

        self._buckets[provider] = RateLimitState(
            tokens=float(burst_size),
            last_update=time.monotonic(),
            max_tokens=float(burst_size),
            refill_rate=refill_rate,
        )

        logger.info(
            "Configured rate limiter",
            provider=provider,
            rpm=requests_per_minute,
            burst=burst_size,
        )

    async def acquire(self, provider: str, tokens: float = 1.0) -> float:
        """Acquire tokens from the bucket, waiting if necessary.

        Args:
            provider: Provider name
            tokens: Number of tokens to acquire

        Returns:
            Wait time in seconds (0 if no wait needed)
        """
        lock = await self._get_lock(provider)

        async with lock:
            if provider not in self._buckets:
                # No rate limit configured
                return 0.0

            bucket = self._buckets[provider]
            now = time.monotonic()

            # Calculate available tokens
            available = bucket.get_available_tokens(now)

            if available >= tokens:
                # Tokens available, consume and return
                bucket.tokens = available - tokens
                bucket.last_update = now
                return 0.0

            # Calculate wait time
            tokens_needed = tokens - available
            wait_time = tokens_needed / (bucket.refill_rate * bucket.adaptive_factor)

            logger.debug(
                "Rate limit wait",
                provider=provider,
                wait_seconds=wait_time,
                tokens_needed=tokens_needed,
            )

            # Wait for tokens
            await asyncio.sleep(wait_time)

            # Update state after wait
            bucket.tokens = 0  # We'll use all accumulated tokens
            bucket.last_update = time.monotonic()

            return wait_time

    async def try_acquire(self, provider: str, tokens: float = 1.0) -> bool:
        """Try to acquire tokens without waiting.

        Args:
            provider: Provider name
            tokens: Number of tokens to acquire

        Returns:
            True if tokens were acquired, False otherwise
        """
        lock = await self._get_lock(provider)

        async with lock:
            if provider not in self._buckets:
                return True

            bucket = self._buckets[provider]
            now = time.monotonic()
            available = bucket.get_available_tokens(now)

            if available >= tokens:
                bucket.tokens = available - tokens
                bucket.last_update = now
                return True

            return False

    async def report_rate_limit(
        self,
        provider: str,
        retry_after: float | None = None,
    ) -> None:
        """Report a rate limit (429) response from provider.

        This triggers adaptive backoff.

        Args:
            provider: Provider name
            retry_after: Retry-After header value in seconds
        """
        lock = await self._get_lock(provider)

        async with lock:
            if provider not in self._buckets:
                return

            bucket = self._buckets[provider]

            # Reduce adaptive factor (slow down requests)
            bucket.adaptive_factor = max(0.1, bucket.adaptive_factor * 0.5)

            # Clear tokens
            bucket.tokens = 0
            bucket.last_update = time.monotonic()

            logger.warning(
                "Rate limit reported, reducing throughput",
                provider=provider,
                adaptive_factor=bucket.adaptive_factor,
                retry_after=retry_after,
            )

            # If retry_after provided, wait that long
            if retry_after and retry_after > 0:
                await asyncio.sleep(retry_after)

    async def report_success(self, provider: str) -> None:
        """Report a successful request.

        This gradually restores the adaptive factor.

        Args:
            provider: Provider name
        """
        lock = await self._get_lock(provider)

        async with lock:
            if provider not in self._buckets:
                return

            bucket = self._buckets[provider]

            # Gradually restore adaptive factor
            if bucket.adaptive_factor < 1.0:
                bucket.adaptive_factor = min(1.0, bucket.adaptive_factor * 1.1)

    def get_status(self, provider: str) -> dict | None:
        """Get current rate limit status for a provider.

        Args:
            provider: Provider name

        Returns:
            Status dict or None if not configured
        """
        if provider not in self._buckets:
            return None

        bucket = self._buckets[provider]
        now = time.monotonic()

        return {
            "available_tokens": bucket.get_available_tokens(now),
            "max_tokens": bucket.max_tokens,
            "refill_rate": bucket.refill_rate,
            "adaptive_factor": bucket.adaptive_factor,
        }


# Singleton instance
rate_limiter = RateLimiter()


def setup_default_limits() -> None:
    """Set up default rate limits for known providers."""
    from stt_service.config import get_settings

    settings = get_settings()

    rate_limiter.configure_provider("gemini", settings.providers.gemini_rpm_limit)
    rate_limiter.configure_provider("elevenlabs", settings.providers.elevenlabs_rpm_limit)
    rate_limiter.configure_provider("whisper", settings.providers.openai_rpm_limit)
    rate_limiter.configure_provider("assemblyai", settings.providers.assemblyai_rpm_limit)
    rate_limiter.configure_provider("deepgram", settings.providers.deepgram_rpm_limit)
    rate_limiter.configure_provider("hispeech", settings.providers.hispeech_rpm_limit)
