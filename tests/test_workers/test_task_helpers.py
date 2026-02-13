"""Tests for helper functions in tasks.py.

Tests _build_transcription_config() and _check_coverage_gap() — pure functions
that need no Celery, DB, or provider infrastructure.
"""

import pytest

from stt_service.core.chunker import ChunkInfo
from stt_service.workers.tasks import _build_transcription_config, _check_coverage_gap


# ===================================================================
# _build_transcription_config
# ===================================================================


class TestBuildTranscriptionConfig:

    def test_defaults_from_none(self):
        config = _build_transcription_config(None)
        assert config.language == "hy"
        assert config.diarization_enabled is True
        assert config.include_timestamps is True

    def test_defaults_from_empty_dict(self):
        config = _build_transcription_config({})
        assert config.language == "hy"
        assert config.additional_languages == ["en", "ru"]

    def test_language_override(self):
        config = _build_transcription_config({"language": "en"})
        assert config.language == "en"

    def test_diarization_settings(self):
        job_config = {
            "diarization": {
                "enabled": False,
                "min_speakers": 2,
                "max_speakers": 5,
            }
        }
        config = _build_transcription_config(job_config)
        assert config.diarization_enabled is False
        assert config.min_speakers == 2
        assert config.max_speakers == 5

    def test_context_settings(self):
        job_config = {
            "context": {
                "prompt": "Medical transcription",
                "custom_vocabulary": ["aspirin", "ibuprofen"],
                "domain": "healthcare",
            }
        }
        config = _build_transcription_config(job_config)
        assert config.prompt == "Medical transcription"
        assert config.custom_vocabulary == ["aspirin", "ibuprofen"]
        assert config.domain == "healthcare"

    def test_output_settings(self):
        job_config = {
            "output": {
                "include_timestamps": False,
                "timestamp_granularity": "word",
                "include_confidence": True,
            }
        }
        config = _build_transcription_config(job_config)
        assert config.include_timestamps is False
        assert config.timestamp_granularity == "word"
        assert config.include_confidence is True

    def test_partial_config(self):
        """Only some fields provided; rest should use defaults."""
        job_config = {"language": "ru", "diarization": {"max_speakers": 3}}
        config = _build_transcription_config(job_config)

        assert config.language == "ru"
        assert config.max_speakers == 3
        assert config.diarization_enabled is True  # default
        assert config.min_speakers is None  # default

    def test_nested_none_values(self):
        """When nested dicts are explicitly None."""
        job_config = {"context": None, "diarization": None, "output": None}
        config = _build_transcription_config(job_config)

        assert config.prompt is None
        assert config.diarization_enabled is True
        assert config.include_timestamps is True


# ===================================================================
# _check_coverage_gap
# ===================================================================


class TestCheckCoverageGap:

    def test_no_segments_returns_full_duration(self):
        """No segments at all — entire chunk is a gap."""
        chunk = _make_chunk(0, 0, 60)
        result = {"segments": []}
        assert _check_coverage_gap(result, chunk) == 60.0

    def test_perfect_coverage_returns_none(self):
        """Segments cover the entire chunk — no gap."""
        chunk = _make_chunk(0, 0, 60)
        result = {
            "segments": [
                {"start_time": 0, "end_time": 30},
                {"start_time": 30, "end_time": 60},
            ]
        }
        assert _check_coverage_gap(result, chunk) is None

    def test_gap_at_start(self):
        """First segment starts late — gap at the beginning."""
        chunk = _make_chunk(0, 0, 60)
        result = {"segments": [{"start_time": 20, "end_time": 60}]}
        gap = _check_coverage_gap(result, chunk)
        assert gap == 20.0

    def test_gap_at_end(self):
        """Last segment ends early — gap at the end."""
        chunk = _make_chunk(0, 0, 60)
        result = {"segments": [{"start_time": 0, "end_time": 40}]}
        gap = _check_coverage_gap(result, chunk)
        assert gap == 20.0

    def test_returns_larger_gap(self):
        """Returns the larger of start gap and end gap."""
        chunk = _make_chunk(0, 0, 100)
        result = {"segments": [{"start_time": 10, "end_time": 70}]}
        gap = _check_coverage_gap(result, chunk)
        # start gap = 10, end gap = 30 → returns 30
        assert gap == 30.0

    def test_timestamp_overflow_handled(self):
        """Segments beyond chunk duration don't produce negative gap."""
        chunk = _make_chunk(0, 0, 60)
        result = {
            "segments": [
                {"start_time": 0, "end_time": 50},
                {"start_time": 50, "end_time": 70},  # overflows chunk by 10s
            ]
        }
        gap = _check_coverage_gap(result, chunk)
        # With overflow handling: last_valid_end is clamped to 60
        # So remaining = 0, start gap = 0 → None
        assert gap is None

    def test_small_gap_still_returned(self):
        """Small gaps (< 15s) are still returned as floats."""
        chunk = _make_chunk(0, 0, 60)
        result = {"segments": [{"start_time": 0, "end_time": 55}]}
        gap = _check_coverage_gap(result, chunk)
        assert gap == 5.0

    def test_missing_segments_key(self):
        """Result dict without segments key returns full duration."""
        chunk = _make_chunk(0, 0, 30)
        result = {}
        gap = _check_coverage_gap(result, chunk)
        assert gap == 30.0


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
