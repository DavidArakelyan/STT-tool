"""Google Gemini STT provider."""

import base64
import mimetypes
from pathlib import Path
from typing import Any

import google.generativeai as genai
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


class GeminiProvider(BaseSTTProvider):
    """Google Gemini multimodal STT provider."""

    name = "gemini"
    max_audio_duration = 600  # 10 minutes
    max_file_size = 100 * 1024 * 1024  # 100MB for Gemini
    supported_formats = ["mp3", "wav", "m4a", "flac", "ogg", "webm", "aac"]

    def __init__(self, api_key: str | None = None) -> None:
        """Initialize Gemini provider."""
        key = api_key or settings.providers.gemini_api_key
        super().__init__(key)
        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel(settings.providers.gemini_model)

    async def transcribe(
        self,
        audio_data: bytes,
        config: TranscriptionConfig,
        audio_format: str = "wav",
    ) -> TranscriptionResponse:
        """Transcribe audio using Gemini multimodal API."""
        try:
            # Get MIME type
            mime_type = self._get_mime_type(audio_format)

            # Build prompt for Gemini
            prompt = self._build_transcription_prompt(config)

            # Create audio part
            audio_part = {
                "mime_type": mime_type,
                "data": base64.b64encode(audio_data).decode("utf-8"),
            }

            # Generate transcription
            response = await self.model.generate_content_async(
                [prompt, audio_part],
                generation_config=genai.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=8192,
                ),
            )

            # Parse response
            return self._parse_response(response, config)

        except Exception as e:
            error_msg = str(e).lower()
            if "quota" in error_msg or "rate" in error_msg or "429" in error_msg:
                raise RateLimitError(
                    message=f"Gemini rate limit exceeded: {e}",
                    provider=self.name,
                    retry_after=60,  # Default 60 seconds
                ) from e
            raise ProviderError(
                message=f"Gemini transcription failed: {e}",
                provider=self.name,
                retryable="temporary" in error_msg or "unavailable" in error_msg,
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

    def _get_mime_type(self, audio_format: str) -> str:
        """Get MIME type for audio format."""
        mime_types = {
            "mp3": "audio/mp3",
            "wav": "audio/wav",
            "m4a": "audio/m4a",
            "flac": "audio/flac",
            "ogg": "audio/ogg",
            "webm": "audio/webm",
            "aac": "audio/aac",
        }
        return mime_types.get(audio_format, mimetypes.guess_type(f"file.{audio_format}")[0] or "audio/wav")

    def _build_transcription_prompt(self, config: TranscriptionConfig) -> str:
        """Build Gemini prompt for transcription."""
        prompt_parts = [
            "Transcribe the following audio accurately.",
            f"Primary language: {self._get_language_name(config.language)}.",
        ]

        if config.additional_languages:
            langs = ", ".join(self._get_language_name(l) for l in config.additional_languages)
            prompt_parts.append(f"The audio may also contain: {langs}.")

        if config.diarization_enabled:
            prompt_parts.append(
                "Identify different speakers and label them as SPEAKER_00, SPEAKER_01, etc."
            )
            if config.max_speakers:
                prompt_parts.append(f"There are at most {config.max_speakers} speakers.")

        if config.custom_vocabulary:
            vocab = ", ".join(config.custom_vocabulary)
            prompt_parts.append(f"Important terms that may appear: {vocab}.")

        if config.prompt:
            prompt_parts.append(f"Context: {config.prompt}")

        prompt_parts.append(
            """
Output format (JSON):
{
  "segments": [
    {
      "speaker": "SPEAKER_00",
      "start": 0.0,
      "end": 5.5,
      "text": "transcribed text here"
    }
  ],
  "full_text": "complete transcription without speaker labels"
}
"""
        )

        return "\n".join(prompt_parts)

    def _get_language_name(self, code: str) -> str:
        """Get language name from ISO code."""
        names = {
            "hy": "Armenian",
            "en": "English",
            "ru": "Russian",
            "fr": "French",
            "de": "German",
            "es": "Spanish",
        }
        return names.get(code, code)

    def _parse_response(
        self,
        response: Any,
        config: TranscriptionConfig,
    ) -> TranscriptionResponse:
        """Parse Gemini response into TranscriptionResponse."""
        import json

        try:
            # Extract text from response
            text = response.text

            # Try to parse as JSON
            # Find JSON in response (it might be wrapped in markdown code blocks)
            json_start = text.find("{")
            json_end = text.rfind("}") + 1

            if json_start >= 0 and json_end > json_start:
                json_str = text[json_start:json_end]
                data = json.loads(json_str)

                segments = []
                for seg in data.get("segments", []):
                    segments.append(
                        TranscriptionSegment(
                            text=seg.get("text", ""),
                            start_time=float(seg.get("start", 0)),
                            end_time=float(seg.get("end", 0)),
                            speaker_id=seg.get("speaker"),
                            confidence=seg.get("confidence"),
                        )
                    )

                full_text = data.get("full_text", "")
                if not full_text and segments:
                    full_text = " ".join(s.text for s in segments)

                return TranscriptionResponse(
                    text=full_text,
                    segments=segments,
                    language_detected=config.language,
                    metadata={"model": settings.providers.gemini_model},
                )

            # Fallback: treat entire response as plain text
            return TranscriptionResponse(
                text=text.strip(),
                segments=[
                    TranscriptionSegment(
                        text=text.strip(),
                        start_time=0.0,
                        end_time=0.0,
                        speaker_id="SPEAKER_00",
                    )
                ],
                language_detected=config.language,
                metadata={"model": settings.providers.gemini_model, "raw_response": True},
            )

        except json.JSONDecodeError:
            # Return plain text if JSON parsing fails
            text = response.text.strip()
            return TranscriptionResponse(
                text=text,
                segments=[
                    TranscriptionSegment(
                        text=text,
                        start_time=0.0,
                        end_time=0.0,
                        speaker_id="SPEAKER_00",
                    )
                ],
                language_detected=config.language,
                metadata={"model": settings.providers.gemini_model, "raw_response": True},
            )

    def supports_language(self, language: str) -> bool:
        """Gemini supports a wide range of languages."""
        # Gemini multimodal supports many languages including Armenian
        return True
