"""Tests for magic-byte file validation."""

import pytest

from stt_service.utils.file_validation import is_valid_media_file


class TestIsValidMediaFile:

    # --- Accepted formats ---

    def test_wav_riff_header(self):
        # RIFF....WAVEfmt (minimal WAV header)
        header = b"RIFF\x00\x00\x00\x00WAVEfmt "
        assert is_valid_media_file(header) is True

    def test_avi_riff_header(self):
        header = b"RIFF\x00\x00\x00\x00AVI LIST"
        assert is_valid_media_file(header) is True

    def test_mp3_id3v2_tag(self):
        header = b"ID3\x04\x00\x00\x00\x00\x00\x00\x00\x00"
        assert is_valid_media_file(header) is True

    def test_mp3_sync_word_fb(self):
        header = b"\xff\xfb\x90\x00" + b"\x00" * 8
        assert is_valid_media_file(header) is True

    def test_mp3_sync_word_fa(self):
        header = b"\xff\xfa\x90\x00" + b"\x00" * 8
        assert is_valid_media_file(header) is True

    def test_mp3_sync_word_f3(self):
        header = b"\xff\xf3\x90\x00" + b"\x00" * 8
        assert is_valid_media_file(header) is True

    def test_mp3_sync_word_f2(self):
        header = b"\xff\xf2\x90\x00" + b"\x00" * 8
        assert is_valid_media_file(header) is True

    def test_aac_adts_mpeg4(self):
        header = b"\xff\xf1\x50\x80" + b"\x00" * 8
        assert is_valid_media_file(header) is True

    def test_aac_adts_mpeg2(self):
        header = b"\xff\xf9\x50\x80" + b"\x00" * 8
        assert is_valid_media_file(header) is True

    def test_mp4_ftyp(self):
        # ftyp box: 4 bytes size + "ftyp" + brand
        header = b"\x00\x00\x00\x18ftypmp42"
        assert is_valid_media_file(header) is True

    def test_m4a_ftyp(self):
        header = b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 4
        assert is_valid_media_file(header) is True

    def test_mov_ftyp(self):
        header = b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 4
        assert is_valid_media_file(header) is True

    def test_3gp_ftyp(self):
        header = b"\x00\x00\x00\x18ftyp3gp5" + b"\x00" * 4
        assert is_valid_media_file(header) is True

    def test_flac(self):
        header = b"fLaC\x00\x00\x00\x22\x10\x00\x10\x00"
        assert is_valid_media_file(header) is True

    def test_ogg(self):
        header = b"OggS\x00\x02\x00\x00\x00\x00\x00\x00"
        assert is_valid_media_file(header) is True

    def test_opus_in_ogg(self):
        # Opus files use OGG container
        header = b"OggS\x00\x02\x00\x00\x00\x00\x00\x00"
        assert is_valid_media_file(header) is True

    def test_webm_ebml(self):
        header = b"\x1a\x45\xdf\xa3\x93\x42\x82\x88webm\x00"
        assert is_valid_media_file(header) is True

    def test_mkv_ebml(self):
        header = b"\x1a\x45\xdf\xa3\x01\x00\x00\x00\x00\x00\x00\x23"
        assert is_valid_media_file(header) is True

    def test_flv(self):
        header = b"FLV\x01\x05\x00\x00\x00\x09\x00\x00\x00"
        assert is_valid_media_file(header) is True

    def test_wma_asf(self):
        header = b"\x30\x26\xb2\x75\x8e\x66\xcf\x11\xa6\xd9\x00\xaa"
        assert is_valid_media_file(header) is True

    def test_wmv_asf(self):
        # WMV uses the same ASF container as WMA
        header = b"\x30\x26\xb2\x75\x8e\x66\xcf\x11\xa6\xd9\x00\xaa"
        assert is_valid_media_file(header) is True

    def test_mpeg_ps(self):
        header = b"\x00\x00\x01\xba\x44\x00\x04\x00\x04\x01\x01\x89"
        assert is_valid_media_file(header) is True

    def test_mpeg1_video(self):
        header = b"\x00\x00\x01\xb3\x00\x01\x00\x01\x00\x00\x00\x00"
        assert is_valid_media_file(header) is True

    def test_mpeg_ts(self):
        header = b"\x47\x40\x00\x10\x00\x00\xb0\x0d\x00\x01\xc1\x00"
        assert is_valid_media_file(header) is True

    # --- Rejected content ---

    def test_rejects_plaintext(self):
        data = b"Hello, this is plain text content."
        assert is_valid_media_file(data) is False

    def test_rejects_html(self):
        data = b"<!DOCTYPE html><html><body></body></html>"
        assert is_valid_media_file(data) is False

    def test_rejects_json(self):
        data = b'{"key": "value", "number": 42}'
        assert is_valid_media_file(data) is False

    def test_rejects_png(self):
        data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        assert is_valid_media_file(data) is False

    def test_rejects_jpeg(self):
        data = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01"
        assert is_valid_media_file(data) is False

    def test_rejects_pdf(self):
        data = b"%PDF-1.4\n1 0 obj\n"
        assert is_valid_media_file(data) is False

    def test_rejects_zip(self):
        data = b"PK\x03\x04\x14\x00\x00\x00\x08\x00\x00\x00"
        assert is_valid_media_file(data) is False

    def test_rejects_elf_binary(self):
        data = b"\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00"
        assert is_valid_media_file(data) is False

    def test_rejects_empty_file(self):
        assert is_valid_media_file(b"") is False

    def test_rejects_too_short(self):
        assert is_valid_media_file(b"\x00\x00") is False
