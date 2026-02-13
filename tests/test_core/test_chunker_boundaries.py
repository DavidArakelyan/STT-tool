"""Tests for AudioChunker.calculate_chunk_boundaries()."""

import pytest

from stt_service.core.chunker import AudioChunker


class TestCalculateChunkBoundaries:
    """Tests for the pure-function boundary calculator (no FFmpeg needed)."""

    def test_short_audio_single_chunk(self):
        """Audio shorter than max_chunk_duration is a single chunk."""
        chunker = AudioChunker(max_chunk_duration=300, overlap_duration=3.0)
        boundaries = chunker.calculate_chunk_boundaries(120.0)

        assert boundaries == [(0.0, 120.0)]

    def test_exact_max_duration_single_chunk(self):
        """Audio exactly equal to max_chunk_duration is a single chunk."""
        chunker = AudioChunker(max_chunk_duration=300, overlap_duration=3.0)
        boundaries = chunker.calculate_chunk_boundaries(300.0)

        assert boundaries == [(0.0, 300.0)]

    def test_two_chunks_with_overlap(self):
        """Audio slightly over max creates two chunks with overlap."""
        chunker = AudioChunker(max_chunk_duration=300, overlap_duration=3.0)
        boundaries = chunker.calculate_chunk_boundaries(400.0)

        assert len(boundaries) == 2
        # First chunk: 0 -> 300
        assert boundaries[0] == (0.0, 300.0)
        # Second chunk starts at 300 - 3 = 297
        assert boundaries[1][0] == 297.0
        assert boundaries[1][1] == 400.0

    def test_three_chunks(self):
        """Longer audio produces three chunks."""
        chunker = AudioChunker(max_chunk_duration=300, overlap_duration=3.0)
        # 300 + (300 - 3) = 597 -> just under 3 chunks
        boundaries = chunker.calculate_chunk_boundaries(700.0)

        assert len(boundaries) == 3
        assert boundaries[0] == (0.0, 300.0)
        assert boundaries[1][0] == 297.0
        assert boundaries[1][1] == 597.0
        assert boundaries[2][0] == 594.0
        assert boundaries[2][1] == 700.0

    def test_overlap_creates_continuity(self):
        """Each chunk's start overlaps with the previous chunk's end."""
        chunker = AudioChunker(max_chunk_duration=100, overlap_duration=5.0)
        boundaries = chunker.calculate_chunk_boundaries(250.0)

        for i in range(1, len(boundaries)):
            prev_end = boundaries[i - 1][1]
            curr_start = boundaries[i][0]
            # Current chunk starts before previous chunk ends
            assert curr_start < prev_end
            # The overlap is exactly overlap_duration
            assert pytest.approx(prev_end - curr_start) == 5.0

    def test_zero_overlap_falls_back_to_settings(self):
        """0.0 is falsy, so AudioChunker.__init__ uses settings default.

        This is a known quirk: ``overlap_duration or settings.default``
        treats 0.0 the same as None.  True zero-overlap would require
        the constructor to use ``if overlap_duration is not None``.
        """
        chunker = AudioChunker(max_chunk_duration=100, overlap_duration=0.0)
        boundaries = chunker.calculate_chunk_boundaries(250.0)

        # Overlap comes from settings (not 0.0), so chunks overlap
        assert len(boundaries) >= 3
        assert boundaries[1][0] < boundaries[0][1]

    def test_very_short_audio(self):
        """Very short audio (< 1 second)."""
        chunker = AudioChunker(max_chunk_duration=300, overlap_duration=3.0)
        boundaries = chunker.calculate_chunk_boundaries(0.5)

        assert boundaries == [(0.0, 0.5)]

    def test_last_chunk_ends_at_duration(self):
        """The last chunk always ends at the total duration."""
        chunker = AudioChunker(max_chunk_duration=100, overlap_duration=5.0)

        for duration in [150.0, 250.0, 500.0, 1000.0]:
            boundaries = chunker.calculate_chunk_boundaries(duration)
            assert boundaries[-1][1] == duration

    def test_all_chunks_within_max_duration(self):
        """No chunk exceeds max_chunk_duration."""
        chunker = AudioChunker(max_chunk_duration=100, overlap_duration=5.0)
        boundaries = chunker.calculate_chunk_boundaries(500.0)

        for start, end in boundaries:
            assert end - start <= 100.0

    def test_full_coverage(self):
        """Every second of audio is covered by at least one chunk."""
        chunker = AudioChunker(max_chunk_duration=100, overlap_duration=5.0)
        total_duration = 350.0
        boundaries = chunker.calculate_chunk_boundaries(total_duration)

        # Check that every point in [0, total_duration] is inside some chunk
        for t in [0, 50, 99, 100, 150, 200, 300, 349, 350]:
            if t > total_duration:
                continue
            covered = any(start <= t <= end for start, end in boundaries)
            assert covered, f"Time {t} not covered by any chunk"
