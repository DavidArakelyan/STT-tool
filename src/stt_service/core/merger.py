"""Transcript merger for combining chunk results."""

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import structlog

from stt_service.config import get_settings
from stt_service.core.chunker import ChunkInfo

logger = structlog.get_logger()
settings = get_settings()


@dataclass
class MergedSegment:
    """A segment in the merged transcript."""

    speaker_id: str
    text: str
    start_time: float
    end_time: float
    confidence: float | None = None
    words: list[dict] | None = None


class TranscriptMerger:
    """Merges transcripts from multiple chunks into a cohesive result.

    Handles:
    - Timestamp adjustment for chunk offsets
    - Overlap deduplication
    - Speaker ID normalization across chunks
    - Text continuity at chunk boundaries
    """

    def __init__(self, overlap_threshold: float = 2.0) -> None:
        """Initialize merger.

        Args:
            overlap_threshold: Time threshold for detecting overlapping segments
        """
        self.overlap_threshold = overlap_threshold

    def merge_transcripts(
        self,
        chunk_results: list[dict[str, Any]],
        chunk_infos: list[ChunkInfo],
    ) -> dict[str, Any]:
        """Merge multiple chunk transcripts into a single transcript.

        Args:
            chunk_results: List of transcription results from each chunk
            chunk_infos: List of chunk metadata with timing info

        Returns:
            Merged transcript dict with full_text, segments, and speakers
        """
        if not chunk_results:
            return {
                "full_text": "",
                "segments": [],
                "speakers": [],
                "warnings": ["No transcription results to merge"],
            }

        if len(chunk_results) == 1:
            # Single chunk, just normalize the format
            return self._format_single_chunk(chunk_results[0])

        # Adjust timestamps and collect all segments
        all_segments = []
        for i, (result, chunk_info) in enumerate(zip(chunk_results, chunk_infos)):
            segments = self._extract_segments(result, chunk_info)
            all_segments.extend(segments)

        # Validate chunk completeness
        validation_warnings = self._validate_chunk_completeness(chunk_results, chunk_infos)
        if validation_warnings:
            logger.warning("Chunk validation warnings detected", warnings=validation_warnings)

        # Sort by start time
        all_segments.sort(key=lambda s: s.start_time)

        # Remove overlapping/duplicate segments
        deduped_segments = self._deduplicate_overlaps(all_segments)

        # Normalize speaker IDs across the merged transcript
        normalized_segments = self._normalize_speakers(deduped_segments)

        # Build full text
        full_text = self._build_full_text(normalized_segments)

        # Compute speaker statistics
        speakers = self._compute_speaker_stats(normalized_segments)

        # Format output
        return {
            "full_text": full_text,
            "segments": [
                {
                    "speaker_id": s.speaker_id,
                    "text": s.text,
                    "start_time": s.start_time,
                    "end_time": s.end_time,
                    "confidence": s.confidence,
                    "words": s.words,
                }
                for s in normalized_segments
            ],
            "speakers": speakers,
            "metadata": {
                "chunks_merged": len(chunk_results),
                "total_segments": len(normalized_segments),
                "dedup_removed": len(all_segments) - len(deduped_segments),
            },
        }

    def _format_single_chunk(self, result: dict[str, Any]) -> dict[str, Any]:
        """Format a single chunk result to standard output format."""
        segments = result.get("segments", [])
        full_text = result.get("text", "")

        if not full_text and segments:
            full_text = " ".join(s.get("text", "") for s in segments)

        # Normalize speakers
        speaker_map = {}
        normalized_segments = []

        for seg in segments:
            speaker = seg.get("speaker_id") or "SPEAKER_00"
            if speaker not in speaker_map:
                speaker_map[speaker] = f"SPEAKER_{len(speaker_map):02d}"
            normalized_speaker = speaker_map[speaker]

            normalized_segments.append({
                "speaker_id": normalized_speaker,
                "text": seg.get("text", ""),
                "start_time": seg.get("start_time", 0),
                "end_time": seg.get("end_time", 0),
                "confidence": seg.get("confidence"),
                "words": seg.get("words"),
            })

        return {
            "full_text": full_text,
            "segments": normalized_segments,
            "speakers": self._compute_speaker_stats_from_dicts(normalized_segments),
            "metadata": {"chunks_merged": 1, "total_segments": len(normalized_segments)},
        }

    def _extract_segments(
        self,
        result: dict[str, Any],
        chunk_info: ChunkInfo,
    ) -> list[MergedSegment]:
        """Extract and timestamp-adjust segments from a chunk result."""
        segments = []
        chunk_offset = chunk_info.start_time

        for seg in result.get("segments", []):
            # Adjust timestamps relative to full audio
            start_time = seg.get("start_time", 0) + chunk_offset
            end_time = seg.get("end_time", 0) + chunk_offset

            # Adjust word timestamps if present
            words = None
            if seg.get("words"):
                words = [
                    {
                        **w,
                        "start_time": w.get("start_time", 0) + chunk_offset,
                        "end_time": w.get("end_time", 0) + chunk_offset,
                    }
                    for w in seg["words"]
                ]

            segments.append(
                MergedSegment(
                    speaker_id=seg.get("speaker_id") or "SPEAKER_00",
                    text=seg.get("text", "").strip(),
                    start_time=start_time,
                    end_time=end_time,
                    confidence=seg.get("confidence"),
                    words=words,
                )
            )

        return segments

    def _deduplicate_overlaps(
        self,
        segments: list[MergedSegment],
    ) -> list[MergedSegment]:
        """Remove duplicate/overlapping segments from chunk boundaries."""
        if not segments:
            return []

        result = [segments[0]]

        for seg in segments[1:]:
            prev = result[-1]

            # Check for overlap
            if seg.start_time < prev.end_time - self.overlap_threshold:
                # Segments overlap significantly
                # Check if texts are similar (likely duplicate from overlap)
                is_similar = self._texts_similar(prev.text, seg.text)
                if is_similar:
                    logger.debug(
                        "Deduplicating segment", 
                        text_prev=prev.text[:30], 
                        text_curr=seg.text[:30],
                        overlap_sec=prev.end_time - seg.start_time
                    )
                    # Keep the longer one
                    if len(seg.text) > len(prev.text):
                        result[-1] = seg
                    continue

                # Different text in overlap - might be different speaker
                # Truncate the previous segment
                if seg.start_time > prev.start_time:
                    logger.debug(
                        "Truncating overlapping segment", 
                        prev_end=prev.end_time,
                        new_end=seg.start_time,
                        next_start=seg.start_time
                    )
                    result[-1] = MergedSegment(
                        speaker_id=prev.speaker_id,
                        text=prev.text,
                        start_time=prev.start_time,
                        end_time=seg.start_time,
                        confidence=prev.confidence,
                        words=prev.words,
                    )

            result.append(seg)

        return result

    def _texts_similar(self, text1: str, text2: str, threshold: float | None = None) -> bool:
        """Check if two texts are similar (for deduplication).

        Uses word overlap for space-separated languages, and falls back to
        character-level comparison for Armenian and other scripts.

        Args:
            text1: First text to compare
            text2: Second text to compare
            threshold: Similarity threshold (0.0-1.0). If None, uses config value (0.8).
                      Increased from 0.7 to reduce false positive deduplication.
        """
        if threshold is None:
            threshold = settings.chunking.overlap_similarity_threshold
        if not text1 or not text2:
            return False
            
        text1_lower = text1.lower().strip()
        text2_lower = text2.lower().strip()
        
        # Exact match
        if text1_lower == text2_lower:
            return True
        
        # Check if one is a substring of the other (common in overlaps)
        if text1_lower in text2_lower or text2_lower in text1_lower:
            shorter = min(len(text1_lower), len(text2_lower))
            longer = max(len(text1_lower), len(text2_lower))
            if shorter / longer >= threshold:
                return True
        
        # Word-level overlap (works for English, Russian)
        words1 = set(text1_lower.split())
        words2 = set(text2_lower.split())

        if words1 and words2:
            intersection = len(words1 & words2)
            union = len(words1 | words2)
            if union > 0 and (intersection / union) >= threshold:
                return True
        
        # Character-level overlap (better for Armenian and concatenated text)
        # Use character trigram comparison
        def get_trigrams(text: str) -> set:
            """Get set of character trigrams from text."""
            text = text.replace(" ", "")  # Remove spaces
            if len(text) < 3:
                return {text} if text else set()
            return {text[i:i+3] for i in range(len(text) - 2)}
        
        trigrams1 = get_trigrams(text1_lower)
        trigrams2 = get_trigrams(text2_lower)
        
        if trigrams1 and trigrams2:
            intersection = len(trigrams1 & trigrams2)
            union = len(trigrams1 | trigrams2)
            if union > 0 and (intersection / union) >= threshold:
                return True
        
        return False

    def _validate_chunk_completeness(
        self,
        chunk_results: list[dict[str, Any]],
        chunk_infos: list[ChunkInfo],
    ) -> list[str]:
        """Validate that chunks appear complete (not truncated).

        Returns list of warning messages for suspicious chunks.
        """
        warnings = []

        for i, (result, chunk_info) in enumerate(zip(chunk_results, chunk_infos)):
            segments = result.get("segments", [])

            # Check 1: Very short transcript for long audio (likely fallback/truncated)
            if chunk_info.duration > 60:  # More than 1 minute
                total_text = result.get("text", "")
                if len(total_text) < 100:  # Less than 100 characters
                    warnings.append(
                        f"Chunk {i}: Suspiciously short transcript ({len(total_text)} chars) "
                        f"for {chunk_info.duration:.1f}s audio"
                    )

            # Check 2: Last segment ends abruptly (no punctuation)
            if segments:
                last_segment_text = segments[-1].get("text", "").strip()
                if last_segment_text and last_segment_text[-1] not in ".!?։":
                    warnings.append(
                        f"Chunk {i}: Last segment doesn't end with punctuation: "
                        f"'{last_segment_text[-50:]}'"
                    )

            # Check 3: Metadata indicates fallback parsing
            metadata = result.get("metadata", {})
            if metadata.get("fallback") == "regex":
                warnings.append(
                    f"Chunk {i}: Used fallback regex parsing (JSON parse failed)"
                )

        return warnings

    def _normalize_speakers(
        self,
        segments: list[MergedSegment],
    ) -> list[MergedSegment]:
        """Normalize speaker IDs to consistent format."""
        # Map all speaker IDs to normalized format
        speaker_map: dict[str, str] = {}

        for seg in segments:
            if seg.speaker_id not in speaker_map:
                # Assign new normalized ID
                speaker_map[seg.speaker_id] = f"SPEAKER_{len(speaker_map):02d}"

        # Apply mapping
        return [
            MergedSegment(
                speaker_id=speaker_map[seg.speaker_id],
                text=seg.text,
                start_time=seg.start_time,
                end_time=seg.end_time,
                confidence=seg.confidence,
                words=seg.words,
            )
            for seg in segments
        ]

    def _build_full_text(self, segments: list[MergedSegment]) -> str:
        """Build full text from segments, merging speaker turns."""
        if not segments:
            return ""

        parts = []
        current_speaker = None

        for seg in segments:
            if seg.speaker_id != current_speaker:
                if parts:
                    parts.append("\n")
                current_speaker = seg.speaker_id

            parts.append(seg.text)
            if not seg.text.endswith((".", "!", "?", ",")):
                parts.append(" ")

        return "".join(parts).strip()

    def _compute_speaker_stats(
        self,
        segments: list[MergedSegment],
    ) -> list[dict[str, Any]]:
        """Compute speaker statistics."""
        stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"duration": 0.0, "segments": 0}
        )

        for seg in segments:
            stats[seg.speaker_id]["duration"] += seg.end_time - seg.start_time
            stats[seg.speaker_id]["segments"] += 1

        return [
            {
                "speaker_id": speaker_id,
                "total_duration": round(data["duration"], 2),
                "segment_count": data["segments"],
            }
            for speaker_id, data in sorted(stats.items())
        ]

    def _compute_speaker_stats_from_dicts(
        self,
        segments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Compute speaker statistics from dict segments."""
        stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"duration": 0.0, "segments": 0}
        )

        for seg in segments:
            speaker_id = seg.get("speaker_id", "SPEAKER_00")
            duration = seg.get("end_time", 0) - seg.get("start_time", 0)
            stats[speaker_id]["duration"] += duration
            stats[speaker_id]["segments"] += 1

        return [
            {
                "speaker_id": speaker_id,
                "total_duration": round(data["duration"], 2),
                "segment_count": data["segments"],
            }
            for speaker_id, data in sorted(stats.items())
        ]
