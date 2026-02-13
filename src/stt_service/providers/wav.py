"""wav.am STT provider - Armenian-optimized speech recognition."""

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


class WavProvider(BaseSTTProvider):
    """wav.am Armenian-optimized STT provider.

    wav.am provides speech-to-text for Armenian, English, and Russian audio.
    API docs: https://wav.am
    """

    name = "wav"
    max_audio_duration = 600
    max_file_size = 50 * 1024 * 1024  # 50MB
    supported_formats = ["mp3", "wav", "m4a", "flac", "ogg", "webm"]

    def __init__(self, api_key: str | None = None) -> None:
        """Initialize wav.am provider."""
        key = api_key or settings.providers.wav_api_key
        super().__init__(key)
        self.base_url = settings.providers.wav_api_url
        self.project_name = settings.providers.wav_project_name
        self._project_id: str | None = None
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": self.api_key},
            timeout=httpx.Timeout(300.0),
        )
        logger.info(
            "Initialized WavProvider", 
            api_key_prefix=self.api_key[:10] if self.api_key else "None", 
            project_name=self.project_name
        )

    async def _ensure_project(self) -> str:
        """Get or create a wav.am project, caching the ID for reuse."""
        if self._project_id:
            return self._project_id

        try:
            # Look for existing project by name
            response = await self.client.post(
                "/get_projects/",
                json={},
            )
            if response.status_code == 200:
                projects = response.json()
                # Handle list response (API returns list of dicts)
                if isinstance(projects, list):
                    for project in projects:
                        if project.get("name") == self.project_name:
                            self._project_id = str(project["id"])
                            logger.info("Found existing wav.am project", project_id=self._project_id, name=self.project_name)
                            return self._project_id
                # Handle potential dict response (future proofing)
                elif isinstance(projects, dict):
                    project_list = projects.get("projects", [])
                    for project in project_list:
                        if project.get("name") == self.project_name:
                            self._project_id = str(project["id"])
                            logger.info("Found existing wav.am project", project_id=self._project_id, name=self.project_name)
                            return self._project_id

            # Project not found — create it
            response = await self.client.post(
                "/add_project/",
                json={"name": self.project_name},
            )
            if response.status_code != 200:
                raise ProviderError(
                    message=f"wav.am add_project failed ({response.status_code}): {response.text}",
                    provider=self.name,
                    retryable=False,
                )

            result = response.json()
            # API returns raw project ID (integer), not a dict
            if isinstance(result, int):
                self._project_id = str(result)
            else:
                self._project_id = str(result.get("project_id", result.get("id", result)))
            logger.info("Created wav.am project", project_id=self._project_id, name=self.project_name)
            return self._project_id

        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(
                message=f"Failed to resolve wav.am project: {e}",
                provider=self.name,
                retryable=True,
            ) from e

    async def transcribe(
        self,
        audio_data: bytes,
        config: TranscriptionConfig,
        audio_format: str = "wav",
    ) -> TranscriptionResponse:
        """Transcribe audio using wav.am API."""
        try:
            project_id = await self._ensure_project()

            files = {
                "audio_file": (f"audio.{audio_format}", audio_data, f"audio/{audio_format}"),
            }

            # wav.am requires a specific language code, not "auto"
            language = config.language if config.language and config.language != "auto" else "hy"

            data: dict[str, str] = {
                "project_id": project_id,
                "language": language,
            }

            # Pass speaker count if configured
            num_speakers = config.max_speakers or 1
            data["num_speakers"] = str(num_speakers)

            logger.info(
                "Sending request to wav.am",
                language=data["language"],
                num_speakers=num_speakers,
                audio_size=len(audio_data),
            )

            response = await self.client.post(
                "/transcribe_audio/",
                files=files,
                data=data,
            )

            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", 60))
                raise RateLimitError(
                    message="wav.am rate limit exceeded",
                    provider=self.name,
                    retry_after=retry_after,
                )

            if response.status_code != 200:
                error_text = response.text
                
                # Check for known non-retryable errors
                is_retryable = response.status_code >= 500
                if response.status_code == 500 and "Failed to transcribe audio" in error_text:
                    # This specific 500 error from wav.am seems permanent for certain files
                    is_retryable = False
                
                raise ProviderError(
                    message=f"wav.am API error ({response.status_code}): {error_text}",
                    provider=self.name,
                    retryable=is_retryable,
                )

            result = response.json()
            return self._parse_response(result, config)

        except (RateLimitError, ProviderError):
            raise
        except httpx.ConnectError as e:
            raise ProviderError(
                message=f"Failed to connect to wav.am API: {e}",
                provider=self.name,
                retryable=True,
            ) from e
        except Exception as e:
            raise ProviderError(
                message=f"wav.am transcription failed: {e}",
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
        result: dict | str | list,
        config: TranscriptionConfig,
    ) -> TranscriptionResponse:
        """Parse wav.am API response.

        wav.am returns a list of speaker segments:
        [{"speaker": "speaker_0", "text": "..."}, ...]
        """
        chunk_duration = config.audio_duration or 0.0
        segments: list[TranscriptionSegment] = []

        if isinstance(result, list):
            # API returns list of {"speaker": "...", "text": "..."}
            for item in result:
                speaker = item.get("speaker", "speaker_0")
                text = item.get("text", "")
                if text:
                    segments.append(
                        TranscriptionSegment(
                            text=text,
                            start_time=0.0,
                            end_time=chunk_duration,
                            speaker_id=speaker,
                        )
                    )
            full_text = " ".join(item.get("text", "") for item in result if item.get("text"))
        elif isinstance(result, str):
            full_text = result
            if full_text:
                segments.append(
                    TranscriptionSegment(
                        text=full_text,
                        start_time=0.0,
                        end_time=chunk_duration,
                        speaker_id="speaker_0",
                    )
                )
        else:
            full_text = result.get("text", "")
            if full_text:
                segments.append(
                    TranscriptionSegment(
                        text=full_text,
                        start_time=0.0,
                        end_time=chunk_duration,
                        speaker_id="speaker_0",
                    )
                )

        return TranscriptionResponse(
            text=full_text,
            segments=segments,
            language_detected=config.language or "hy",
            metadata={"provider": "wav"},
        )

    def supports_language(self, language: str) -> bool:
        """Check language support."""
        return language in ["hy", "en", "ru"]

    def supports_diarization(self) -> bool:
        """wav.am supports speaker diarization via num_speakers."""
        return True

    async def health_check(self) -> bool:
        """Check wav.am API availability."""
        return bool(self.api_key)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()
