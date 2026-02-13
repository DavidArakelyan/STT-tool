"""Tests for WavProvider._parse_response()."""

import pytest

from stt_service.providers.base import TranscriptionConfig
from stt_service.providers.wav import WavProvider


@pytest.fixture
def provider():
    """Create WavProvider with a dummy key (no API calls made)."""
    return WavProvider(api_key="test-key")


@pytest.fixture
def config():
    """Default transcription config."""
    return TranscriptionConfig(language="hy", audio_duration=15.0)


class TestWavParseResponse:
    """Tests for wav.am response parsing."""

    def test_list_of_speaker_segments(self, provider, config):
        """wav.am returns [{"speaker": "...", "text": "..."}, ...]."""
        result = [
            {"speaker": "speaker_1", "text": "Hello world"},
            {"speaker": "speaker_0", "text": "How are you"},
        ]
        response = provider._parse_response(result, config)

        assert response.text == "Hello world How are you"
        assert len(response.segments) == 2
        assert response.segments[0].speaker_id == "speaker_1"
        assert response.segments[0].text == "Hello world"
        assert response.segments[1].speaker_id == "speaker_0"
        assert response.segments[1].text == "How are you"

    def test_list_with_speaker_diarization(self, provider, config):
        """Speaker IDs are preserved from the API response."""
        result = [
            {"speaker": "speaker_0", "text": "Hello"},
            {"speaker": "speaker_1", "text": "Hi there"},
            {"speaker": "speaker_0", "text": "How are you"},
        ]
        response = provider._parse_response(result, config)

        assert len(response.segments) == 3
        assert response.segments[0].speaker_id == "speaker_0"
        assert response.segments[1].speaker_id == "speaker_1"
        assert response.segments[2].speaker_id == "speaker_0"
        assert response.text == "Hello Hi there How are you"

    def test_empty_list(self, provider, config):
        """Empty list returns empty response."""
        response = provider._parse_response([], config)

        assert response.text == ""
        assert response.segments == []

    def test_list_with_empty_text_filtered(self, provider, config):
        """Segments with empty text are filtered out."""
        result = [
            {"speaker": "speaker_0", "text": "Hello"},
            {"speaker": "speaker_1", "text": ""},
            {"speaker": "speaker_0", "text": "World"},
        ]
        response = provider._parse_response(result, config)

        assert len(response.segments) == 2
        assert response.text == "Hello World"

    def test_string_response(self, provider, config):
        """Plain string response (fallback format)."""
        response = provider._parse_response("Just plain text", config)

        assert response.text == "Just plain text"
        assert len(response.segments) == 1
        assert response.segments[0].speaker_id == "speaker_0"

    def test_empty_string_response(self, provider, config):
        """Empty string returns empty response."""
        response = provider._parse_response("", config)

        assert response.text == ""
        assert response.segments == []

    def test_dict_response_with_text(self, provider, config):
        """Dict response with text key (legacy format)."""
        result = {"text": "Some transcription"}
        response = provider._parse_response(result, config)

        assert response.text == "Some transcription"
        assert len(response.segments) == 1

    def test_dict_response_empty_text(self, provider, config):
        """Dict with empty text returns empty response."""
        result = {"text": ""}
        response = provider._parse_response(result, config)

        assert response.text == ""
        assert response.segments == []

    def test_segment_timestamps_match_config(self, provider, config):
        """Segment timestamps use config.audio_duration."""
        result = [{"speaker": "speaker_0", "text": "Hello"}]
        response = provider._parse_response(result, config)

        assert response.segments[0].start_time == 0.0
        assert response.segments[0].end_time == 15.0

    def test_metadata_includes_provider(self, provider, config):
        """Response metadata identifies the provider."""
        result = [{"speaker": "speaker_0", "text": "Hello"}]
        response = provider._parse_response(result, config)

        assert response.metadata == {"provider": "wav"}

    def test_language_detected(self, provider, config):
        """Language detected matches config."""
        result = [{"speaker": "speaker_0", "text": "Hello"}]
        response = provider._parse_response(result, config)

        assert response.language_detected == "hy"

    def test_missing_speaker_defaults(self, provider, config):
        """Missing speaker key defaults to speaker_0."""
        result = [{"text": "Hello"}]
        response = provider._parse_response(result, config)

        assert response.segments[0].speaker_id == "speaker_0"

    def test_none_audio_duration(self, provider):
        """When audio_duration is None, end_time defaults to 0.0."""
        config = TranscriptionConfig(language="en", audio_duration=None)
        result = [{"speaker": "speaker_0", "text": "Hello"}]
        response = provider._parse_response(result, config)

        assert response.segments[0].end_time == 0.0

    def test_single_item_list(self, provider, config):
        """Single-item list works correctly."""
        result = [{"speaker": "speaker_0", "text": "Only one segment"}]
        response = provider._parse_response(result, config)

        assert response.text == "Only one segment"
        assert len(response.segments) == 1
