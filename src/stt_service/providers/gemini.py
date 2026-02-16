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
        import time
        start_time = time.time()
        
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

            logger.info(
                "========== GEMINI PROMPT ==========",
                chunk_index=config.chunk_index,
                prompt=prompt,
            )

            # Create audio part
            audio_part = {
                "mime_type": mime_type,
                "data": base64.b64encode(audio_data).decode("utf-8"),
            }

            # Generate transcription
            # Gemini 3 is optimized for temperature=1.0 (default)
            # Build response schema with duration constraints
            segment_properties = {
                "speaker": {
                    "type": "string",
                    "description": "Speaker identifier (e.g., SPEAKER_00, SPEAKER_01)"
                },
                "start": {
                    "type": "number",
                    "description": "Segment start time in seconds"
                },
                "end": {
                    "type": "number",
                    "description": "Segment end time in seconds"
                },
                "text": {
                    "type": "string",
                    "description": "Transcribed text for this segment"
                }
            }

            generation_config = genai.GenerationConfig(
                temperature=settings.providers.gemini_temperature,
                max_output_tokens=settings.providers.gemini_max_output_tokens,
                response_mime_type="application/json",
                # JSON schema for structured output validation
                response_schema={
                    "type": "object",
                    "properties": {
                        "segments": {
                            "type": "array",
                            "description": "Array of transcribed segments with speaker identification and timestamps",
                            "items": {
                                "type": "object",
                                "properties": segment_properties,
                                "required": ["speaker", "start", "end", "text"]
                            }
                        }
                    },
                    "required": ["segments"]  # Removed full_text to save ~50% tokens
                }
            )

            response = await self.model.generate_content_async(
                [prompt, audio_part],
                generation_config=generation_config,
                request_options={"timeout": settings.providers.gemini_request_timeout},
            )

            # Calculate processing latency
            processing_latency_ms = int((time.time() - start_time) * 1000)

            logger.info(
                "========== GEMINI RESPONSE ==========",
                chunk_index=config.chunk_index,
                latency_ms=processing_latency_ms,
                response_text=response.text if hasattr(response, 'text') else "",
            )

            # Extract finish reason
            finish_reason_str = "UNKNOWN"
            finish_reason_value = None
            if response.candidates:
                finish_reason = response.candidates[0].finish_reason
                finish_reason_value = finish_reason if isinstance(finish_reason, int) else finish_reason.value
                # Map finish reason value to string
                finish_reason_map = {1: "STOP", 2: "MAX_TOKENS", 3: "SAFETY", 4: "RECITATION", 5: "OTHER"}
                finish_reason_str = finish_reason_map.get(finish_reason_value, f"UNKNOWN_{finish_reason_value}")

                # FinishReason.MAX_TOKENS = 2 (not 3!)
                if finish_reason_value == 2:
                    logger.error(
                        "Gemini response truncated due to token limit",
                        chunk_index=config.chunk_index,
                        audio_duration=config.audio_duration,
                        finish_reason=finish_reason,
                    )
                    raise ProviderError(
                        message=f"Transcription truncated: exceeded {generation_config.max_output_tokens} tokens. "
                                f"Chunk too long ({config.audio_duration:.1f}s). Reduce chunk size.",
                        provider=self.name,
                        retryable=False,
                    )
                elif finish_reason_value not in [1, 2]:  # 1 = STOP, 2 = MAX_TOKENS
                    logger.warning(
                        "Gemini finished with unexpected reason",
                        finish_reason=finish_reason,
                        finish_reason_value=finish_reason_value,
                        chunk_index=config.chunk_index,
                    )

            # Extract token usage metrics
            input_tokens = 0
            output_tokens = 0
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                usage = response.usage_metadata
                input_tokens = getattr(usage, 'prompt_token_count', 0) or 0
                output_tokens = getattr(usage, 'candidates_token_count', 0) or 0

                logger.info(
                    "========== GEMINI TOKENS ==========",
                    chunk_index=config.chunk_index,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    max_allowed=generation_config.max_output_tokens,
                    utilization_pct=round(100 * output_tokens / generation_config.max_output_tokens, 1) if output_tokens else 0,
                )

                # Warn if approaching limit (>80%)
                if output_tokens > 0.8 * generation_config.max_output_tokens:
                    logger.warning(
                        "Approaching token limit",
                        chunk_index=config.chunk_index,
                        audio_duration=config.audio_duration,
                    )

            # Build metadata dict
            raw_response_text = response.text if hasattr(response, 'text') else ""
            response_metadata = {
                "model": settings.providers.gemini_model,
                "prompt": prompt,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "processing_latency_ms": processing_latency_ms,
                "finish_reason": finish_reason_str,
                "raw_response": raw_response_text,
            }

            # Parse response
            # Use provided duration from ffmpeg metadata if available, otherwise fallback to estimate
            if config.audio_duration:
                duration_est = config.audio_duration
            else:
                 # Fallback: 16-bit 16kHz mono = 32KB/s
                 duration_est = len(audio_data) / 32000

            return self._parse_response(response, config, duration=duration_est, extra_metadata=response_metadata)

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
                retryable=(
                    "temporary" in error_lower
                    or "unavailable" in error_lower
                    or "deadline" in error_lower
                    or "timeout" in error_lower
                    or "504" in error_lower
                    or "503" in error_lower
                    or "500" in error_lower
                ),
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

    def _build_transcription_prompt_new(self, config: TranscriptionConfig) -> str:
        """Build structured Gemini prompt for transcription."""
        sections = []

        # --- SYSTEM ROLE ---
        primary_lang = self._get_language_name(config.language) if config.language and config.language.lower() != "auto" else None
        additional_langs = [self._get_language_name(l) for l in config.additional_languages] if config.additional_languages else []

        if primary_lang and additional_langs:
            lang_desc = f"{primary_lang}-multilingual"
        elif primary_lang:
            lang_desc = primary_lang
        else:
            lang_desc = "multilingual"

        sections.append(
            f"### SYSTEM ROLE\n"
            f"You are an expert {lang_desc} transcriber. "
            f"Your ONLY goal is to capture every single word spoken in the PROVIDED AUDIO CLIP. "
            f"Completeness is more important than timestamp precision — timestamps will be corrected automatically."
        )

        # --- VOICE REFERENCE LIBRARY (only for chunk > 0 with context) ---
        if config.previous_transcript_context and config.chunk_index > 0:
            speakers_str = ""
            if config.previous_speakers:
                speakers_str = ", ".join(f'"{s}"' for s in config.previous_speakers)
                speakers_str = f" Use it ONLY to match {speakers_str} to their respective voices."

            sections.append(
                f"### VOICE REFERENCE LIBRARY (FOR SPEAKER IDENTIFICATION ONLY)\n"
                f"The following text is from the PREVIOUS chunk.{speakers_str}\n"
                f"[DO NOT TRANSCRIBE THIS CONTENT. DO NOT START YOUR TRANSCRIPTION FROM WHERE THIS LEAVES OFF.]\n\n"
                f"--- START REFERENCE ---\n"
                f"{config.previous_transcript_context}\n"
                f"--- END REFERENCE ---"
            )

            logger.info(
                "Injecting context into prompt",
                chunk_index=config.chunk_index,
                context_length=len(config.previous_transcript_context),
            )

        # --- CURRENT TASK ---
        duration = config.audio_duration or 0.0
        chunk_label = f"CHUNK {config.chunk_index + 1}" if config.chunk_index > 0 else "FULL CLIP"

        task_lines = [f"### CURRENT TASK: {chunk_label}"]
        task_lines.append(
            f"1. **Target Audio**: Transcribe the ENTIRE {duration:.1f}s audio clip. "
            f"Do NOT stop early. Every word from the first second to the last must appear."
        )

        if config.previous_transcript_context and config.chunk_index > 0:
            task_lines.append(
                "2. **Independence**: Treat the audio as a fresh start. "
                "If the speaker repeats words heard in the reference above, "
                "you MUST transcribe them again in the new segments."
            )
        else:
            task_lines.append(
                "2. **Independence**: Transcribe every word in the audio from beginning to end."
            )

        # Language instructions
        lang_parts = []
        if primary_lang:
            lang_parts.append(f"Primary is {primary_lang}.")
        else:
            lang_parts.append("Detect the primary language.")
        if additional_langs:
            lang_parts.append(f"Include any {', '.join(additional_langs)} phrases exactly as spoken.")
        task_lines.append(f"3. **Language**: {' '.join(lang_parts)}")

        # Speaker ID instructions
        if config.diarization_enabled:
            speaker_note = "Assign SPEAKER_00, SPEAKER_01, etc."
            if config.previous_speakers:
                speaker_ids = ", ".join(config.previous_speakers)
                speaker_note = (
                    f"Assign {speaker_ids} based on the voices matched "
                    f"in the reference library above."
                )
            if config.max_speakers:
                speaker_note += f" There are at most {config.max_speakers} speakers."
            task_lines.append(f"4. **Speaker ID**: {speaker_note}")

        # Optional context / domain / vocabulary
        extras = []
        if config.custom_vocabulary:
            vocab = ", ".join(config.custom_vocabulary)
            extras.append(f"5. **Key Terms**: {vocab}")
        if config.prompt:
            extras.append(f"{'6' if extras else '5'}. **Context**: {config.prompt}")
        if config.domain:
            idx = len(extras) + 5
            extras.append(f"{idx}. **Domain**: {config.domain}")
        task_lines.extend(extras)

        sections.append("\n".join(task_lines))

        # --- CONSTRAINTS ---
        constraint_lines = ["### CONSTRAINTS"]
        constraint_lines.append(
            '- **Completeness over timing**: Capture every word. '
            'Approximate timestamps are fine — they will be aligned in post-processing.'
        )
        constraint_lines.append(
            '- **No Summarization**: Every filler word, stutter, and hesitation must be captured.'
        )
        constraint_lines.append("- **Output**: Valid JSON only.")
        sections.append("\n".join(constraint_lines))

        # --- OUTPUT FORMAT ---
        sections.append(
            '### OUTPUT FORMAT\n'
            '{\n'
            '  "segments": [\n'
            '    {\n'
            '      "speaker": "SPEAKER_XX",\n'
            '      "start": 0.0,\n'
            '      "end": 0.0,\n'
            '      "text": "..."\n'
            '    }\n'
            '  ]\n'
            '}'
        )

        return "\n\n".join(sections)

    def _build_transcription_prompt(self, config: TranscriptionConfig) -> str:
        """Legacy prompt builder (pre-structured format). Kept for reference/rollback."""
        prompt_parts = [
            "Transcribe the following audio accurately.",
        ]

        if config.audio_duration:
            prompt_parts.append(
                f"\nThis audio clip is approximately {config.audio_duration:.1f} seconds long. "
                f"Provide approximate timestamps for each segment. "
                f"Completeness is more important than timestamp precision — "
                f"transcribe ALL spoken content even if timing is approximate."
            )

        if config.previous_transcript_context and config.chunk_index > 0:
            prompt_parts.append(
                f"\n**IMPORTANT - This is a CONTINUATION of a longer recording (chunk {config.chunk_index + 1}).** "
                f"The conversation was already in progress. Here is the recent transcript for context:\n"
                f"---\n{config.previous_transcript_context}\n---\n"
                f"Continue transcribing from where this left off. Maintain speaker consistency with the context above.\n"
                f"**IMPORTANT: The audio may start with content already captured in the context above — "
                f"this is intentional overlap for continuity. Transcribe ALL audio content "
                f"from the very beginning of this clip (timestamp 0.0). The system will "
                f"automatically handle any overlap during merging.**"
            )
            if config.previous_speakers:
                speakers_str = ", ".join(config.previous_speakers)
                prompt_parts.append(f"Known speakers from previous context: {speakers_str}. Reuse these IDs for the same voices.")

            logger.info("Injecting context into prompt", chunk_index=config.chunk_index, context_length=len(config.previous_transcript_context))

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
  ]
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
            "auto": "Auto Detect",
        }
        return names.get(code, code)

    def _parse_response(
        self,
        response: Any,
        config: TranscriptionConfig,
        duration: float = 0.0,
        extra_metadata: dict[str, Any] | None = None,
    ) -> TranscriptionResponse:
        """Parse Gemini response into TranscriptionResponse."""
        import json
        import re

        try:
            # Extract text from response
            text = response.text
            if not text:
                raise ProviderError("Gemini returned empty response")
            
            # Debug log raw response (full)
            logger.info("Gemini raw response text", full_text=text, total_length=len(text))

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
                # Strict Validation
                self._validate_json_structure(data)

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

                # Alignment pass: rescale timestamps to fit within chunk duration
                if duration > 0 and segments:
                    segments = self._align_timestamps(segments, duration, config.chunk_index)

                # Reconstruct full_text from segments (Gemini no longer provides it to save tokens)
                full_text = " ".join(s.text for s in segments) if segments else ""

                if full_text or segments:
                    # Merge extra_metadata with base metadata
                    metadata = {"model": settings.providers.gemini_model}
                    if extra_metadata:
                        metadata.update(extra_metadata)
                    return TranscriptionResponse(
                        text=full_text,
                        segments=segments,
                        language_detected=config.language,
                        metadata=metadata,
                    )

            # Fallback: RegExp Extraction instead of raw dump
            # If JSON parsing failed, try to extract 'text' fields using regex
            # Pattern matches: "text": "..."
            logger.warning("Falling back to regex extraction for Gemini response")

            # Check if response appears truncated
            if not text.rstrip().endswith('}') or text.count('{') != text.count('}'):
                logger.error(
                    "Gemini response appears truncated (malformed JSON)",
                    ends_with=text[-50:] if len(text) > 50 else text,
                )

            # Require closing quote (don't accept truncated text)
            matches = re.findall(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"\s*[,}]', text, re.DOTALL)

            if not matches:
                # If strict regex fails, log and raise error
                logger.error(
                    "Failed to extract any text from Gemini response",
                    response_length=len(text),
                    response_preview=text[:500],
                )
                raise ProviderError(
                    "Failed to parse Gemini response: JSON is malformed and regex extraction failed. "
                    "This may indicate truncated output."
                )

            if matches:
                def unescape_string(s):
                    try:
                        # Try standard JSON unescape first
                        return json.loads(f'"{s}"')
                    except Exception:
                        # Fallback: manual unescape of common JSON escapes
                        # This avoids the unicode_escape latin-1 pitfall that causes mojibake
                        return s.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')

                full_text = " ".join(unescape_string(match) for match in matches)
                # Merge extra_metadata with fallback metadata
                metadata = {"model": settings.providers.gemini_model, "fallback": "regex"}
                if extra_metadata:
                    metadata.update(extra_metadata)
                return TranscriptionResponse(
                    text=full_text,
                    segments=[
                        TranscriptionSegment(
                            text=full_text,
                            start_time=0.0,
                            end_time=duration,  # Use estimated duration (better than 0)
                            speaker_id="SPEAKER_00",
                        )
                    ],
                    language_detected=config.language,
                    metadata=metadata,
                )

            # Last resort: Plain text cleanup
            clean_text = text.strip()
            clean_text = re.sub(r"```[a-z]*\n?", "", clean_text)
            clean_text = clean_text.replace("```", "").strip()
            # If it still looks like JSON, it's probably broken JSON, but we shouldn't show it as transcript
            if clean_text.startswith("{") and "segments" in clean_text:
                 # It's broken JSON and regex failed.
                 raise ProviderError("Failed to parse Gemini response: Invalid JSON and regex extraction failed.")

            # Merge extra_metadata with raw_response metadata
            metadata = {"model": settings.providers.gemini_model, "raw_response": True}
            if extra_metadata:
                metadata.update(extra_metadata)
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
                metadata=metadata,
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
    def _align_timestamps(
        self,
        segments: list[TranscriptionSegment],
        duration: float,
        chunk_index: int,
    ) -> list[TranscriptionSegment]:
        """Align segment timestamps to fit within the chunk duration.

        If all timestamps already fit, this is a no-op.
        If timestamps overflow, they are proportionally rescaled so that
        the last segment ends at `duration` while preserving relative ordering.
        Negative starts are clamped to 0.
        """
        if not segments:
            return segments

        max_end = max(s.end_time for s in segments)
        min_start = min(s.start_time for s in segments)

        needs_rescale = max_end > duration * 1.05  # 5% tolerance
        needs_negative_fix = min_start < 0

        if not needs_rescale and not needs_negative_fix:
            return segments

        logger.info(
            "========== TIMESTAMP ALIGNMENT ==========",
            chunk_index=chunk_index,
            original_range=f"{min_start:.1f}-{max_end:.1f}",
            target_range=f"0.0-{duration:.1f}",
            rescale=needs_rescale,
        )

        scale = (duration / max_end) if needs_rescale and max_end > 0 else 1.0

        aligned = []
        for seg in segments:
            start = max(0.0, seg.start_time * scale)
            end = min(duration, seg.end_time * scale)
            # Ensure end > start
            if end <= start:
                end = min(start + 0.1, duration)
            aligned.append(
                TranscriptionSegment(
                    text=seg.text,
                    start_time=round(start, 2),
                    end_time=round(end, 2),
                    speaker_id=seg.speaker_id,
                    confidence=seg.confidence,
                    words=seg.words,
                )
            )

        return aligned

    def _validate_json_structure(self, data: dict[str, Any]) -> None:
        """Validate that the parsed JSON matches the expected schema.
        
        Args:
            data: The parsed JSON dictionary.
            
        Raises:
            ProviderError: If validation fails (caught by caller to trigger fallback).
        """
        if "segments" not in data:
            logger.warning("JSON validation failed: missing 'segments' key")
            raise ProviderError("JSON validation failed: missing 'segments' key")
            
        segments = data.get("segments")
        if not isinstance(segments, list):
            logger.warning("JSON validation failed: 'segments' is not a list")
            raise ProviderError("JSON validation failed: 'segments' is not a list")
            
        for i, seg in enumerate(segments):
            if not isinstance(seg, dict):
                 raise ProviderError(f"Segment {i} is not a dict")
            
            required_keys = ["speaker", "start", "end", "text"]
            for key in required_keys:
                if key not in seg:
                    logger.warning(f"JSON validation failed: segment {i} missing '{key}'", segment=seg)
                    raise ProviderError(f"Segment {i} missing required key: {key}")
            
            # Type checks (loose validation, direct casting happens in parse)
            if not isinstance(seg.get("start"), (int, float, str)) or not isinstance(seg.get("end"), (int, float, str)):
                 logger.warning(f"JSON validation failed: segment {i} timestamps invalid", segment=seg)
                 raise ProviderError(f"Segment {i} timestamps invalid type")
