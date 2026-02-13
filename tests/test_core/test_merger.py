"""Tests for TranscriptMerger."""

import pytest

from stt_service.core.chunker import ChunkInfo
from stt_service.core.merger import MergedSegment, TranscriptMerger


@pytest.fixture
def merger():
    return TranscriptMerger(overlap_threshold=2.0)


# ---------------------------------------------------------------------------
# merge_transcripts — empty / single chunk
# ---------------------------------------------------------------------------

class TestMergeTranscriptsBasic:

    def test_empty_results(self, merger):
        result = merger.merge_transcripts([], [])
        assert result["text"] == ""
        assert result["segments"] == []
        assert "No transcription results to merge" in result["warnings"]

    def test_single_chunk_passes_through(self, merger):
        chunk_result = {
            "chunk_full_text": "Hello world",
            "segments": [
                {
                    "speaker_id": "speaker_0",
                    "text": "Hello world",
                    "start_time": 0.0,
                    "end_time": 5.0,
                }
            ],
        }
        result = merger.merge_transcripts([chunk_result], [_make_chunk(0, 0, 5)])

        assert result["text"] == "Hello world"
        assert len(result["segments"]) == 1
        assert result["segments"][0]["speaker_id"] == "SPEAKER_00"

    def test_single_chunk_normalizes_speakers(self, merger):
        chunk_result = {
            "chunk_full_text": "",
            "segments": [
                {"speaker_id": "spk_abc", "text": "A", "start_time": 0, "end_time": 1},
                {"speaker_id": "spk_xyz", "text": "B", "start_time": 1, "end_time": 2},
                {"speaker_id": "spk_abc", "text": "C", "start_time": 2, "end_time": 3},
            ],
        }
        result = merger.merge_transcripts([chunk_result], [_make_chunk(0, 0, 3)])

        ids = [s["speaker_id"] for s in result["segments"]]
        assert ids == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"]


# ---------------------------------------------------------------------------
# merge_transcripts — multi-chunk
# ---------------------------------------------------------------------------

class TestMergeTranscriptsMultiChunk:

    def test_two_chunks_timestamps_adjusted(self, merger):
        """Segment timestamps are adjusted by chunk offset."""
        chunk1 = {
            "segments": [
                {"speaker_id": "s0", "text": "First chunk", "start_time": 0, "end_time": 5}
            ],
        }
        chunk2 = {
            "segments": [
                {"speaker_id": "s0", "text": "Second chunk", "start_time": 0, "end_time": 5}
            ],
        }
        info1 = _make_chunk(0, 0, 10)
        info2 = _make_chunk(1, 10, 20)

        result = merger.merge_transcripts([chunk1, chunk2], [info1, info2])

        # First segment stays at 0-5, second should be offset to 10-15
        assert result["segments"][0]["start_time"] == 0.0
        assert result["segments"][1]["start_time"] == 10.0
        assert result["segments"][1]["end_time"] == 15.0
        assert result["metadata"]["chunks_merged"] == 2

    def test_speakers_normalized_across_chunks(self, merger):
        """Speaker IDs from different chunks map to SPEAKER_XX consistently."""
        chunk1 = {
            "segments": [
                {"speaker_id": "speaker_0", "text": "A", "start_time": 0, "end_time": 5}
            ],
        }
        chunk2 = {
            "segments": [
                {"speaker_id": "speaker_0", "text": "B", "start_time": 0, "end_time": 5}
            ],
        }
        info1 = _make_chunk(0, 0, 10)
        info2 = _make_chunk(1, 10, 20)

        result = merger.merge_transcripts([chunk1, chunk2], [info1, info2])

        # Same raw speaker_id across chunks -> same normalized ID
        ids = {s["speaker_id"] for s in result["segments"]}
        assert ids == {"SPEAKER_00"}


# ---------------------------------------------------------------------------
# _build_full_text
# ---------------------------------------------------------------------------

class TestBuildFullText:

    def test_single_speaker(self, merger):
        segments = [
            MergedSegment("SPEAKER_00", "Hello", 0, 1),
            MergedSegment("SPEAKER_00", "World", 1, 2),
        ]
        text = merger._build_full_text(segments)
        assert text == "SPEAKER_00: Hello World"

    def test_alternating_speakers(self, merger):
        segments = [
            MergedSegment("SPEAKER_00", "Hi.", 0, 1),
            MergedSegment("SPEAKER_01", "Hello.", 1, 2),
        ]
        text = merger._build_full_text(segments)
        lines = text.split("\n")
        assert len(lines) == 2
        assert lines[0] == "SPEAKER_00: Hi."
        assert lines[1] == "SPEAKER_01: Hello."

    def test_empty_segments(self, merger):
        assert merger._build_full_text([]) == ""


# ---------------------------------------------------------------------------
# _normalize_speakers
# ---------------------------------------------------------------------------

class TestNormalizeSpeakers:

    def test_maps_in_order_of_appearance(self, merger):
        segments = [
            MergedSegment("zebra", "A", 0, 1),
            MergedSegment("alpha", "B", 1, 2),
            MergedSegment("zebra", "C", 2, 3),
        ]
        result = merger._normalize_speakers(segments)

        assert result[0].speaker_id == "SPEAKER_00"
        assert result[1].speaker_id == "SPEAKER_01"
        assert result[2].speaker_id == "SPEAKER_00"


# ---------------------------------------------------------------------------
# _texts_similar
# ---------------------------------------------------------------------------

class TestTextsSimilar:

    def test_exact_match(self, merger):
        assert merger._texts_similar("hello world", "hello world") is True

    def test_case_insensitive(self, merger):
        assert merger._texts_similar("Hello World", "hello world") is True

    def test_empty_strings(self, merger):
        assert merger._texts_similar("", "") is False
        assert merger._texts_similar("hello", "") is False

    def test_completely_different(self, merger):
        assert merger._texts_similar("abc def ghi", "xyz uvw rst") is False

    def test_substring_match(self, merger):
        # "hello" is substring of "hello world", length ratio 5/11 < 0.8 => not similar
        assert merger._texts_similar("hello", "hello world") is False

    def test_high_overlap(self, merger):
        assert merger._texts_similar(
            "the quick brown fox",
            "the quick brown dog",
            threshold=0.6,
        ) is True


# ---------------------------------------------------------------------------
# _deduplicate_overlaps
# ---------------------------------------------------------------------------

class TestDeduplicateOverlaps:

    def test_no_overlap(self, merger):
        segments = [
            MergedSegment("s0", "First", 0, 5),
            MergedSegment("s0", "Second", 5, 10),
        ]
        result = merger._deduplicate_overlaps(segments)
        assert len(result) == 2

    def test_duplicate_text_removed(self, merger):
        """When two segments overlap with the same text, one is removed."""
        segments = [
            MergedSegment("s0", "Hello world", 0, 10),
            MergedSegment("s0", "Hello world", 5, 15),  # starts before prev.end - threshold
        ]
        result = merger._deduplicate_overlaps(segments)
        assert len(result) == 1

    def test_empty_input(self, merger):
        assert merger._deduplicate_overlaps([]) == []


# ---------------------------------------------------------------------------
# _compute_speaker_stats
# ---------------------------------------------------------------------------

class TestComputeSpeakerStats:

    def test_basic_stats(self, merger):
        segments = [
            MergedSegment("SPEAKER_00", "A", 0, 5),
            MergedSegment("SPEAKER_01", "B", 5, 8),
            MergedSegment("SPEAKER_00", "C", 8, 12),
        ]
        stats = merger._compute_speaker_stats(segments)

        stats_map = {s["speaker_id"]: s for s in stats}
        assert stats_map["SPEAKER_00"]["total_duration"] == 9.0
        assert stats_map["SPEAKER_00"]["segment_count"] == 2
        assert stats_map["SPEAKER_01"]["total_duration"] == 3.0
        assert stats_map["SPEAKER_01"]["segment_count"] == 1


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_chunk(index: int, start: float, end: float) -> ChunkInfo:
    return ChunkInfo(
        index=index,
        start_time=start,
        end_time=end,
        duration=end - start,
    )
