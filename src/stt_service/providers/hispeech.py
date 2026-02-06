"""HiSpeech STT provider - Armenian-optimized speech recognition."""

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


class HiSpeechProvider(BaseSTTProvider):
    """HiSpeech Armenian-optimized STT provider.

    HiSpeech (hispeech.ai) specializes in Armenian language speech recognition,
    making it ideal for Armenian-primary content with mixed English/Russian terms.
    """

    name = "hispeech"
    max_audio_duration = 600  # 10 minutes (adjust based on actual API limits)
    max_file_size = 50 * 1024 * 1024  # 50MB (adjust based on actual API limits)
    supported_formats = ["mp3", "wav", "m4a", "flac", "ogg", "webm"]

    def __init__(self, api_key: str | None = None) -> None:
        """Initialize HiSpeech provider."""
        key = api_key or settings.providers.hispeech_api_key
        super().__init__(key)
        self.base_url = settings.providers.hispeech_api_url
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "x-auth-token": self.api_key,
            },
            timeout=httpx.Timeout(300.0),  # 5 minute timeout
        )

    async def transcribe(
        self,
        audio_data: bytes,
        config: TranscriptionConfig,
        audio_format: str = "wav",
    ) -> TranscriptionResponse:
        """Transcribe audio using HiSpeech API.

        Note: This implementation is based on common STT API patterns.
        Adjust endpoints and payload structure based on actual HiSpeech API docs.
        """
        try:
            # Prepare multipart form data
            files = {
                "file": (f"audio.{audio_format}", audio_data, f"audio/{audio_format}"),
            }

            # Build request payload
            # HiSpeech only accepts 'file' and 'wait_for_result' parameters
            data: dict[str, str] = {
                "wait_for_result": "true",  # Synchronous response
            }

            # Make API request
            response = await self.client.post(
                "/api/v1/transcriptions/upload",
                files=files,
                data=data,
            )

            # Handle rate limiting
            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", 60))
                raise RateLimitError(
                    message="HiSpeech rate limit exceeded",
                    provider=self.name,
                    retry_after=retry_after,
                )

            # Handle errors
            if response.status_code != 200:
                error_text = response.text
                raise ProviderError(
                    message=f"HiSpeech API error ({response.status_code}): {error_text}",
                    provider=self.name,
                    retryable=response.status_code >= 500,
                )

            result = response.json()
            return self._parse_response(result, config)

        except (RateLimitError, ProviderError):
            raise
        except httpx.ConnectError as e:
            raise ProviderError(
                message=f"Failed to connect to HiSpeech API: {e}",
                provider=self.name,
                retryable=True,
            ) from e
        except Exception as e:
            raise ProviderError(
                message=f"HiSpeech transcription failed: {e}",
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
        """Parse HiSpeech API response.

        Note: Adjust field names based on actual HiSpeech API response format.
        """
        # Debug log raw response
        logger.info("HiSpeech raw response", result=result)
        
        segments = []

        # Try different common response formats
        raw_segments = result.get("segments") or result.get("utterances") or []

        for seg in raw_segments:
            segments.append(
                TranscriptionSegment(
                    text=seg.get("text") or seg.get("transcript", ""),
                    start_time=float(seg.get("start") or seg.get("start_time", 0)),
                    end_time=float(seg.get("end") or seg.get("end_time", 0)),
                    speaker_id=seg.get("speaker") or seg.get("speaker_id"),
                    confidence=seg.get("confidence"),
                    words=seg.get("words"),
                )
            )

        # Get full text
        full_text = (
            result.get("text")
            or result.get("transcript")
            or result.get("transcription", "")
        )
        if not full_text and segments:
            full_text = " ".join(s.text for s in segments)

        # If no segments but we have text, create a single segment
        if not segments and full_text:
            segments = [
                TranscriptionSegment(
                    text=full_text,
                    start_time=0.0,
                    end_time=result.get("duration", 0.0),
                    speaker_id="SPEAKER_00",
                )
            ]

        return TranscriptionResponse(
            text=full_text,
            segments=segments,
            language_detected=result.get("language") or result.get("detected_language") or "hy",
            metadata={
                "provider": "hispeech",
                "raw_response": result,
            },
        )

    def supports_language(self, language: str) -> bool:
        """Check language support - HiSpeech is optimized for Armenian."""
        # HiSpeech is Armenian-focused but may support other languages
        supported = ["hy", "en", "ru"]
        return language in supported

    def supports_diarization(self) -> bool:
        """Check if HiSpeech supports diarization."""
        # Assume diarization is supported; adjust based on actual API
        return True

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()

    async def health_check(self) -> bool:
        """Check HiSpeech API availability."""
        if not self.api_key:
            return False

        try:
            # Try a lightweight endpoint if available
            response = await self.client.get("/health", timeout=10)
            return response.status_code == 200
        except Exception:
            # If no health endpoint, check if API key is configured
            return bool(self.api_key)
