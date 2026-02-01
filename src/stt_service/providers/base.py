"""Base STT provider interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TranscriptionConfig:
    """Configuration passed to providers for transcription."""

    # Language settings
    language: str = "hy"  # Armenian
    additional_languages: list[str] = field(default_factory=lambda: ["en", "ru"])

    # Context
    prompt: str | None = None
    custom_vocabulary: list[str] = field(default_factory=list)
    domain: str | None = None

    # Context injection for multi-chunk processing
    previous_transcript_context: str | None = None  # Last N segments as text
    previous_speakers: list[str] = field(default_factory=list)  # Known speaker IDs
    chunk_index: int = 0  # Current chunk index (0 for first/single chunk)

    # Diarization
    diarization_enabled: bool = True
    min_speakers: int | None = None
    max_speakers: int | None = None

    # Output options
    include_timestamps: bool = True
    timestamp_granularity: str = "segment"  # "segment" or "word"
    include_confidence: bool = False


@dataclass
class TranscriptionSegment:
    """A single transcribed segment."""

    text: str
    start_time: float
    end_time: float
    speaker_id: str | None = None
    confidence: float | None = None
    words: list[dict[str, Any]] | None = None


@dataclass
class TranscriptionResponse:
    """Response from a provider transcription call."""

    # Full transcript text
    text: str

    # Segments with timestamps and speaker info
    segments: list[TranscriptionSegment]

    # Detected language
    language_detected: str | None = None

    # Provider-specific metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    # Processing duration
    processing_time_ms: int | None = None


class BaseSTTProvider(ABC):
    """Abstract base class for STT providers."""

    # Provider name identifier
    name: str = "base"

    # Maximum audio duration this provider can handle in one request (seconds)
    max_audio_duration: int = 600  # 10 minutes default

    # Maximum file size in bytes
    max_file_size: int = 25 * 1024 * 1024  # 25MB default

    # Supported audio formats
    supported_formats: list[str] = ["mp3", "wav", "m4a", "flac", "ogg", "webm"]

    def __init__(self, api_key: str) -> None:
        """Initialize provider with API key."""
        self.api_key = api_key

    @abstractmethod
    async def transcribe(
        self,
        audio_data: bytes,
        config: TranscriptionConfig,
        audio_format: str = "wav",
    ) -> TranscriptionResponse:
        """Transcribe audio data.

        Args:
            audio_data: Raw audio bytes
            config: Transcription configuration
            audio_format: Format of the audio data

        Returns:
            TranscriptionResponse with text and segments

        Raises:
            ProviderError: If transcription fails
            RateLimitError: If rate limit is exceeded
        """
        pass

    @abstractmethod
    async def transcribe_file(
        self,
        file_path: str,
        config: TranscriptionConfig,
    ) -> TranscriptionResponse:
        """Transcribe audio from a file path.

        Args:
            file_path: Path to audio file
            config: Transcription configuration

        Returns:
            TranscriptionResponse with text and segments
        """
        pass

    async def health_check(self) -> bool:
        """Check if the provider is available and configured.

        Returns:
            True if provider is healthy
        """
        return bool(self.api_key)

    def supports_diarization(self) -> bool:
        """Check if provider supports speaker diarization.

        Returns:
            True if diarization is supported
        """
        return True

    def supports_language(self, language: str) -> bool:
        """Check if provider supports a specific language.

        Args:
            language: ISO 639-1 language code

        Returns:
            True if language is supported
        """
        # By default, assume Armenian, English, Russian are supported
        return language in ["hy", "en", "ru"]

    def build_prompt(self, config: TranscriptionConfig) -> str:
        """Build a prompt string from configuration.

        Args:
            config: Transcription configuration

        Returns:
            Prompt string for the provider
        """
        parts = []

        if config.prompt:
            parts.append(config.prompt)

        if config.domain:
            parts.append(f"Domain: {config.domain}")

        if config.custom_vocabulary:
            vocab_str = ", ".join(config.custom_vocabulary)
            parts.append(f"Custom terms: {vocab_str}")

        if config.additional_languages:
            langs = ", ".join(config.additional_languages)
            parts.append(
                f"The audio may contain mixed languages: {config.language} (primary), {langs}"
            )

        return ". ".join(parts) if parts else ""

    def _normalize_segments(
        self,
        segments: list[TranscriptionSegment],
        time_offset: float = 0.0,
    ) -> list[TranscriptionSegment]:
        """Normalize segment timestamps with an offset.

        Args:
            segments: List of segments
            time_offset: Time offset to add to all timestamps

        Returns:
            Segments with adjusted timestamps
        """
        if time_offset == 0.0:
            return segments

        return [
            TranscriptionSegment(
                text=seg.text,
                start_time=seg.start_time + time_offset,
                end_time=seg.end_time + time_offset,
                speaker_id=seg.speaker_id,
                confidence=seg.confidence,
                words=[
                    {**w, "start_time": w["start_time"] + time_offset, "end_time": w["end_time"] + time_offset}
                    for w in (seg.words or [])
                ]
                if seg.words
                else None,
            )
            for seg in segments
        ]
