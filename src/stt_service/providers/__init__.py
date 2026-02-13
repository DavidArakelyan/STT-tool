"""STT Providers package."""

from stt_service.providers.base import (
    BaseSTTProvider,
    TranscriptionConfig,
    TranscriptionResponse,
    TranscriptionSegment,
)
from stt_service.providers.elevenlabs import ElevenLabsProvider
from stt_service.providers.gemini import GeminiProvider
from stt_service.providers.hispeech import HiSpeechProvider
from stt_service.providers.wav import WavProvider
from stt_service.providers.whisper import WhisperProvider

__all__ = [
    "BaseSTTProvider",
    "TranscriptionConfig",
    "TranscriptionResponse",
    "TranscriptionSegment",
    "GeminiProvider",
    "ElevenLabsProvider",
    "WhisperProvider",
    "HiSpeechProvider",
    "WavProvider",
    "get_provider",
    "ProviderFactory",
]


class ProviderFactory:
    """Factory for creating STT provider instances."""

    _providers: dict[str, type[BaseSTTProvider]] = {
        "gemini": GeminiProvider,
        "elevenlabs": ElevenLabsProvider,
        "whisper": WhisperProvider,
        "hispeech": HiSpeechProvider,
        "wav": WavProvider,
    }

    @classmethod
    def get_provider(cls, name: str, api_key: str | None = None) -> BaseSTTProvider:
        """Get a provider instance by name.

        Args:
            name: Provider name (gemini, elevenlabs, whisper, hispeech)
            api_key: Optional API key override

        Returns:
            Provider instance

        Raises:
            ValueError: If provider name is unknown
        """
        provider_class = cls._providers.get(name.lower())
        if not provider_class:
            available = ", ".join(cls._providers.keys())
            raise ValueError(f"Unknown provider: {name}. Available: {available}")

        return provider_class(api_key)

    @classmethod
    def list_providers(cls) -> list[str]:
        """List available provider names."""
        return list(cls._providers.keys())

    @classmethod
    def register_provider(
        cls,
        name: str,
        provider_class: type[BaseSTTProvider],
    ) -> None:
        """Register a custom provider.

        Args:
            name: Provider name
            provider_class: Provider class
        """
        cls._providers[name.lower()] = provider_class


def get_provider(name: str, api_key: str | None = None) -> BaseSTTProvider:
    """Convenience function to get a provider instance."""
    return ProviderFactory.get_provider(name, api_key)
