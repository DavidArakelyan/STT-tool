"""ElevenLabs STT provider."""

from pathlib import Path

import httpx
import structlog

from stt_service.config import get_settings
from stt_service.providers.base import (
    BaseSTTProvider,
    TranscriptionConfig,
    TranscriptionResponse,
    TranscriptionSegment,
)
from stt_service.utils.exceptions import ProviderError, RateLimitError

logger = structlog.get_logger()
settings = get_settings()


class ElevenLabsProvider(BaseSTTProvider):
    """ElevenLabs Speech-to-Text provider."""

    name = "elevenlabs"
    max_audio_duration = 600  # 10 minutes
    max_file_size = 50 * 1024 * 1024  # 50MB
    supported_formats = ["mp3", "wav", "m4a", "flac", "ogg", "webm"]

    # ElevenLabs API endpoints
    BASE_URL = "https://api.elevenlabs.io/v1"
    SCRIBE_ENDPOINT = "/speech-to-text"

    def __init__(self, api_key: str | None = None) -> None:
        """Initialize ElevenLabs provider."""
        key = api_key or settings.providers.elevenlabs_api_key
        super().__init__(key)
        self.client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={
                "xi-api-key": self.api_key,
            },
            timeout=httpx.Timeout(300.0),  # 5 minute timeout for long audio
        )

    async def transcribe(
        self,
        audio_data: bytes,
        config: TranscriptionConfig,
        audio_format: str = "wav",
    ) -> TranscriptionResponse:
        """Transcribe audio using ElevenLabs Scribe API."""
        try:
            # Build request - ElevenLabs expects "file" parameter
            files = {
                "file": (f"audio.{audio_format}", audio_data, f"audio/{audio_format}"),
            }

            data = {
                "model_id": "scribe_v1",
                "language_code": self._map_language_code(config.language),
            }

            # Add diarization settings
            if config.diarization_enabled:
                data["diarize"] = "true"
                if config.max_speakers:
                    data["num_speakers"] = str(config.max_speakers)

            # Add timestamps (map granularity to ElevenLabs values)
            if config.include_timestamps:
                # ElevenLabs accepts: 'none', 'word', 'character'
                granularity_map = {
                    "segment": "word",  # Map segment to word for ElevenLabs
                    "word": "word",
                    "character": "character",
                }
                data["timestamps_granularity"] = granularity_map.get(
                    config.timestamp_granularity, "word"
                )

            response = await self.client.post(
                self.SCRIBE_ENDPOINT,
                files=files,
                data=data,
            )

            # Handle rate limiting
            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", 60))
                raise RateLimitError(
                    message="ElevenLabs rate limit exceeded",
                    provider=self.name,
                    retry_after=retry_after,
                )

            if response.status_code != 200:
                error_text = response.text
                raise ProviderError(
                    message=f"ElevenLabs API error ({response.status_code}): {error_text}",
                    provider=self.name,
                    retryable=response.status_code >= 500,
                )

            result = response.json()
            return self._parse_response(result, config)

        except (RateLimitError, ProviderError):
            raise
        except Exception as e:
            raise ProviderError(
                message=f"ElevenLabs transcription failed: {e}",
                provider=self.name,
                retryable=True,
            ) from e

    async def transcribe_file(
        self,
        file_path: str,
        config: TranscriptionConfig,
    ) -> TranscriptionResponse:
        """Transcribe audio from a file."""
        path = Path(file_path)
        audio_format = path.suffix.lstrip(".").lower()
        audio_data = path.read_bytes()
        return await self.transcribe(audio_data, config, audio_format)

    def _map_language_code(self, language: str) -> str:
        """Map ISO 639-1 to ElevenLabs language codes."""
        # ElevenLabs uses ISO 639-1 codes
        return language

    def _parse_response(
        self,
        result: dict,
        config: TranscriptionConfig,
    ) -> TranscriptionResponse:
        """Parse ElevenLabs response."""
        segments = []

        # Parse segments/words from response
        if "segments" in result:
            for seg in result["segments"]:
                segments.append(
                    TranscriptionSegment(
                        text=seg.get("text", ""),
                        start_time=float(seg.get("start", 0)),
                        end_time=float(seg.get("end", 0)),
                        speaker_id=seg.get("speaker_id"),
                        confidence=seg.get("confidence"),
                        words=seg.get("words"),
                    )
                )
        elif "words" in result:
            # Word-level transcription without segments
            current_speaker = "SPEAKER_00"
            current_text = []
            current_start = 0.0
            current_end = 0.0

            for word in result["words"]:
                word_speaker = word.get("speaker_id", current_speaker)

                if word_speaker != current_speaker and current_text:
                    segments.append(
                        TranscriptionSegment(
                            text=" ".join(current_text),
                            start_time=current_start,
                            end_time=current_end,
                            speaker_id=current_speaker,
                        )
                    )
                    current_text = []
                    current_start = word.get("start", 0)

                current_speaker = word_speaker
                current_text.append(word.get("text", ""))
                current_end = word.get("end", current_end)

                if not current_text or len(current_text) == 1:
                    current_start = word.get("start", current_start)

            # Add final segment
            if current_text:
                segments.append(
                    TranscriptionSegment(
                        text=" ".join(current_text),
                        start_time=current_start,
                        end_time=current_end,
                        speaker_id=current_speaker,
                    )
                )

        # Get full text
        full_text = result.get("text", "")
        if not full_text and segments:
            full_text = " ".join(s.text for s in segments)

        return TranscriptionResponse(
            text=full_text,
            segments=segments,
            language_detected=result.get("language_code"),
            metadata={
                "model": "scribe_v1",
                "provider_response": result,
            },
        )

    def supports_diarization(self) -> bool:
        """ElevenLabs supports speaker diarization."""
        return True

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()
