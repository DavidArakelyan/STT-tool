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

            logger.info(
                "Gemini API request",
                audio_size=len(audio_data),
                mime_type=mime_type,
                model=settings.providers.gemini_model,
            )

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

            logger.info("Gemini API response received", has_text=bool(response.text))

            # Parse response
            return self._parse_response(response, config)

        except Exception as e:
            error_msg = str(e)
            error_type = type(e).__name__
            
            # Log the full error details
            logger.error(
                "Gemini API error",
                error_type=error_type,
                error_message=error_msg,
                audio_size=len(audio_data),
            )
            
            error_lower = error_msg.lower()
            if "quota" in error_lower or "rate" in error_lower or "429" in error_lower or "resource_exhausted" in error_lower:
                raise RateLimitError(
                    message=f"Gemini rate limit exceeded: {error_msg}",
                    provider=self.name,
                    retry_after=60,  # Default 60 seconds
                ) from e
            raise ProviderError(
                message=f"Gemini transcription failed: {error_msg}",
                provider=self.name,
                retryable="temporary" in error_lower or "unavailable" in error_lower,
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
        ]

        # Add context from previous chunks for better continuity
        if config.previous_transcript_context and config.chunk_index > 0:
            prompt_parts.append(
                f"\n**IMPORTANT - This is a CONTINUATION of a longer recording (chunk {config.chunk_index + 1}).** "
                f"The conversation was already in progress. Here is the recent transcript for context:\n"
                f"---\n{config.previous_transcript_context}\n---\n"
                f"Continue transcribing from where this left off. Maintain speaker consistency with the context above."
            )
            if config.previous_speakers:
                speakers_str = ", ".join(config.previous_speakers)
                prompt_parts.append(f"Known speakers from previous context: {speakers_str}. Reuse these IDs for the same voices.")

        if config.language and config.language.lower() != "auto":
             prompt_parts.append(f"Primary language: {self._get_language_name(config.language)}.")
        else:
             prompt_parts.append("Detect the primary language and transcribe.")

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
        names = {
            "hy": "Armenian",
            "en": "English",
            "ru": "Russian",
            "fr": "French",
            "de": "German",
            "es": "Spanish",
            "auto": "Auto Detect",
        }
        return names.get(code, code)

    def _parse_response(
        self,
        response: Any,
        config: TranscriptionConfig,
    ) -> TranscriptionResponse:
        """Parse Gemini response into TranscriptionResponse."""
        import json
        import re

        try:
            # Extract text from response
            text = response.text
            if not text:
                raise ProviderError("Gemini returned empty response")

            # Try to find JSON in response (it might be wrapped in markdown code blocks)
            # Find the first { and the last }
            json_match = re.search(r"\{.*\}", text, re.DOTALL)
            
            data = None
            if json_match:
                try:
                    json_str = json_match.group(0)
                    data = json.loads(json_str)
                except json.JSONDecodeError:
                    # Try cleaning common issues like trailing commas or markdown escapes
                    cleaned_json = re.sub(r",\s*([\]\}])", r"\1", json_str)
                    try:
                        data = json.loads(cleaned_json)
                    except json.JSONDecodeError:
                        logger.warning("Failed to parse Gemini JSON after cleaning", text=text)

            if data and isinstance(data, dict):
                segments_data = data.get("segments", [])
                segments = []
                for seg in segments_data:
                    segments.append(
                        TranscriptionSegment(
                            text=seg.get("text", "").strip(),
                            start_time=float(seg.get("start", 0)),
                            end_time=float(seg.get("end", 0)),
                            speaker_id=seg.get("speaker", "SPEAKER_00"),
                            confidence=seg.get("confidence"),
                        )
                    )

                full_text = data.get("full_text", "")
                if not full_text and segments:
                    full_text = " ".join(s.text for s in segments)

                if full_text or segments:
                    return TranscriptionResponse(
                        text=full_text,
                        segments=segments,
                        language_detected=config.language,
                        metadata={"model": settings.providers.gemini_model},
                    )

            # Fallback: treat entire response as plain text, but clean up markdown blocks
            clean_text = text.strip()
            # Remove markdown code blocks like ```json ... ``` or just ``` ... ```
            clean_text = re.sub(r"```[a-z]*\n?", "", clean_text)
            clean_text = clean_text.replace("```", "").strip()

            return TranscriptionResponse(
                text=clean_text,
                segments=[
                    TranscriptionSegment(
                        text=clean_text,
                        start_time=0.0,
                        end_time=0.0,
                        speaker_id="SPEAKER_00",
                    )
                ],
                language_detected=config.language,
                metadata={"model": settings.providers.gemini_model, "raw_response": True},
            )

        except Exception as e:
            logger.error("Error parsing Gemini response", error=str(e), text=response.text[:500] if hasattr(response, 'text') else str(response))
            # Last resort fallback
            fallback_text = str(response.text) if hasattr(response, 'text') else "Error parsing response"
            return TranscriptionResponse(
                text=fallback_text,
                segments=[],
                language_detected=config.language,
                metadata={"error": str(e)},
            )

    def supports_language(self, language: str) -> bool:
        """Gemini supports a wide range of languages."""
        # Gemini multimodal supports many languages including Armenian
        return True
