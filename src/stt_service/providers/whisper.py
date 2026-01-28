"""OpenAI Whisper STT provider."""

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


class WhisperProvider(BaseSTTProvider):
    """OpenAI Whisper STT provider."""

    name = "whisper"
    max_audio_duration = 600  # ~10 minutes
    max_file_size = 25 * 1024 * 1024  # 25MB OpenAI limit
    supported_formats = ["mp3", "wav", "m4a", "flac", "ogg", "webm", "mp4", "mpeg", "mpga"]

    BASE_URL = "https://api.openai.com/v1"

    def __init__(self, api_key: str | None = None) -> None:
        """Initialize Whisper provider."""
        key = api_key or settings.providers.openai_api_key
        super().__init__(key)
        self.model = settings.providers.openai_model
        self.client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
            },
            timeout=httpx.Timeout(300.0),
        )

    async def transcribe(
        self,
        audio_data: bytes,
        config: TranscriptionConfig,
        audio_format: str = "wav",
    ) -> TranscriptionResponse:
        """Transcribe audio using OpenAI Whisper API."""
        try:
            # Build multipart form data
            files = {
                "file": (f"audio.{audio_format}", audio_data, f"audio/{audio_format}"),
            }

            data = {
                "model": self.model,
                "response_format": "verbose_json",
            }

            # Set language if specified (helps accuracy)
            if config.language:
                data["language"] = config.language

            # Add prompt for context
            prompt = self.build_prompt(config)
            if prompt:
                data["prompt"] = prompt

            # Request word-level timestamps if needed
            if config.timestamp_granularity == "word":
                data["timestamp_granularities"] = ["word", "segment"]

            response = await self.client.post(
                "/audio/transcriptions",
                files=files,
                data=data,
            )

            # Handle rate limiting
            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", 60))
                raise RateLimitError(
                    message="OpenAI rate limit exceeded",
                    provider=self.name,
                    retry_after=retry_after,
                )

            if response.status_code != 200:
                error_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {"error": response.text}
                raise ProviderError(
                    message=f"OpenAI API error ({response.status_code}): {error_data}",
                    provider=self.name,
                    retryable=response.status_code >= 500,
                )

            result = response.json()
            return self._parse_response(result, config)

        except (RateLimitError, ProviderError):
            raise
        except Exception as e:
            raise ProviderError(
                message=f"Whisper transcription failed: {e}",
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

    def _parse_response(
        self,
        result: dict,
        config: TranscriptionConfig,
    ) -> TranscriptionResponse:
        """Parse OpenAI Whisper response."""
        segments = []

        for seg in result.get("segments", []):
            words = None
            if "words" in result and config.timestamp_granularity == "word":
                # Match words to this segment by time
                seg_words = [
                    w for w in result["words"]
                    if w.get("start", 0) >= seg.get("start", 0)
                    and w.get("end", 0) <= seg.get("end", 0)
                ]
                if seg_words:
                    words = [
                        {
                            "text": w.get("word", ""),
                            "start_time": w.get("start", 0),
                            "end_time": w.get("end", 0),
                        }
                        for w in seg_words
                    ]

            segments.append(
                TranscriptionSegment(
                    text=seg.get("text", "").strip(),
                    start_time=float(seg.get("start", 0)),
                    end_time=float(seg.get("end", 0)),
                    speaker_id="SPEAKER_00",  # Whisper doesn't do diarization
                    confidence=seg.get("avg_logprob"),
                    words=words,
                )
            )

        return TranscriptionResponse(
            text=result.get("text", "").strip(),
            segments=segments,
            language_detected=result.get("language"),
            metadata={
                "model": self.model,
                "duration": result.get("duration"),
            },
        )

    def supports_diarization(self) -> bool:
        """Whisper doesn't support speaker diarization natively."""
        return False

    def supports_language(self, language: str) -> bool:
        """Whisper supports many languages including Armenian."""
        # Whisper supports 99 languages including Armenian (hy)
        return True

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()
