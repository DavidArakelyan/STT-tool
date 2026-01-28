"""Retry strategies with exponential backoff."""

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any, ParamSpec, TypeVar

import structlog

from stt_service.config import get_settings
from stt_service.services.rate_limiter import rate_limiter
from stt_service.utils.exceptions import ProviderError, RateLimitError

logger = structlog.get_logger()
settings = get_settings()

P = ParamSpec("P")
T = TypeVar("T")


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""

    max_retries: int = 5
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    jitter_max: float = 1.0

    @classmethod
    def from_settings(cls) -> "RetryConfig":
        """Create config from application settings."""
        return cls(
            max_retries=settings.retry.max_retries,
            base_delay=settings.retry.base_delay,
            max_delay=settings.retry.max_delay,
            exponential_base=settings.retry.exponential_base,
            jitter_max=settings.retry.jitter_max,
        )


def calculate_delay(
    attempt: int,
    config: RetryConfig,
    rate_limit_delay: float | None = None,
) -> float:
    """Calculate delay before next retry.

    Args:
        attempt: Current attempt number (0-indexed)
        config: Retry configuration
        rate_limit_delay: Override from rate limit response

    Returns:
        Delay in seconds
    """
    if rate_limit_delay is not None:
        # Use rate limit delay with small jitter
        return rate_limit_delay + random.uniform(0, config.jitter_max)

    # Exponential backoff with jitter
    delay = min(
        config.max_delay,
        config.base_delay * (config.exponential_base ** attempt),
    )
    jitter = random.uniform(0, config.jitter_max)

    return delay + jitter


async def retry_with_backoff(
    func: Callable[P, Awaitable[T]],
    *args: P.args,
    config: RetryConfig | None = None,
    provider: str | None = None,
    on_retry: Callable[[int, Exception, float], Awaitable[None]] | None = None,
    **kwargs: P.kwargs,
) -> T:
    """Execute function with retry and exponential backoff.

    Args:
        func: Async function to call
        *args: Positional arguments for func
        config: Retry configuration (uses defaults if None)
        provider: Provider name for rate limiting
        on_retry: Optional callback called before each retry
        **kwargs: Keyword arguments for func

    Returns:
        Result from successful function call

    Raises:
        Last exception if all retries exhausted
    """
    if config is None:
        config = RetryConfig.from_settings()

    last_exception: Exception | None = None

    for attempt in range(config.max_retries + 1):
        try:
            # Acquire rate limit token if provider specified
            if provider:
                await rate_limiter.acquire(provider)

            result = await func(*args, **kwargs)

            # Report success for adaptive rate limiting
            if provider:
                await rate_limiter.report_success(provider)

            return result

        except RateLimitError as e:
            last_exception = e

            if provider:
                await rate_limiter.report_rate_limit(provider, e.retry_after)

            if attempt >= config.max_retries:
                logger.error(
                    "Max retries exceeded (rate limit)",
                    provider=provider,
                    attempts=attempt + 1,
                )
                raise

            # Use retry_after from exception if available
            delay = calculate_delay(attempt, config, e.retry_after)

            logger.warning(
                "Rate limit hit, retrying",
                provider=provider,
                attempt=attempt + 1,
                max_retries=config.max_retries,
                delay=delay,
            )

            if on_retry:
                await on_retry(attempt, e, delay)

            await asyncio.sleep(delay)

        except ProviderError as e:
            last_exception = e

            if not e.retryable:
                logger.error(
                    "Non-retryable provider error",
                    provider=e.provider,
                    error=str(e),
                )
                raise

            if attempt >= config.max_retries:
                logger.error(
                    "Max retries exceeded (provider error)",
                    provider=e.provider,
                    attempts=attempt + 1,
                    error=str(e),
                )
                raise

            delay = calculate_delay(attempt, config)

            logger.warning(
                "Provider error, retrying",
                provider=e.provider,
                attempt=attempt + 1,
                max_retries=config.max_retries,
                delay=delay,
                error=str(e),
            )

            if on_retry:
                await on_retry(attempt, e, delay)

            await asyncio.sleep(delay)

        except Exception as e:
            last_exception = e

            if attempt >= config.max_retries:
                logger.error(
                    "Max retries exceeded (unexpected error)",
                    attempts=attempt + 1,
                    error=str(e),
                )
                raise

            delay = calculate_delay(attempt, config)

            logger.warning(
                "Unexpected error, retrying",
                attempt=attempt + 1,
                max_retries=config.max_retries,
                delay=delay,
                error=str(e),
            )

            if on_retry:
                await on_retry(attempt, e, delay)

            await asyncio.sleep(delay)

    # Should never reach here, but just in case
    if last_exception:
        raise last_exception
    raise RuntimeError("Retry loop completed without result or exception")


def with_retry(
    config: RetryConfig | None = None,
    provider: str | None = None,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Decorator for adding retry behavior to async functions.

    Args:
        config: Retry configuration
        provider: Provider name for rate limiting

    Returns:
        Decorator function
    """

    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            return await retry_with_backoff(
                func,
                *args,
                config=config,
                provider=provider,
                **kwargs,
            )

        return wrapper

    return decorator


class RetryContext:
    """Context manager for tracking retry state.

    Useful when you need more control over the retry loop.
    """

    def __init__(
        self,
        config: RetryConfig | None = None,
        provider: str | None = None,
    ) -> None:
        self.config = config or RetryConfig.from_settings()
        self.provider = provider
        self.attempt = 0
        self.last_error: Exception | None = None
        self.total_delay = 0.0

    def should_retry(self, error: Exception) -> bool:
        """Check if we should retry after an error.

        Args:
            error: The exception that was raised

        Returns:
            True if we should retry
        """
        self.last_error = error
        self.attempt += 1

        if self.attempt > self.config.max_retries:
            return False

        if isinstance(error, ProviderError) and not error.retryable:
            return False

        return True

    async def wait_before_retry(self) -> float:
        """Wait before the next retry attempt.

        Returns:
            The delay that was applied
        """
        rate_limit_delay = None
        if isinstance(self.last_error, RateLimitError):
            rate_limit_delay = self.last_error.retry_after
            if self.provider:
                await rate_limiter.report_rate_limit(self.provider, rate_limit_delay)

        delay = calculate_delay(self.attempt - 1, self.config, rate_limit_delay)
        self.total_delay += delay

        logger.info(
            "Waiting before retry",
            attempt=self.attempt,
            delay=delay,
            total_delay=self.total_delay,
        )

        await asyncio.sleep(delay)
        return delay

    async def acquire_rate_limit(self) -> None:
        """Acquire rate limit token if provider is configured."""
        if self.provider:
            await rate_limiter.acquire(self.provider)

    async def report_success(self) -> None:
        """Report successful completion."""
        if self.provider:
            await rate_limiter.report_success(self.provider)
