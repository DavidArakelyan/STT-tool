"""File content validation via magic bytes.

Verifies that uploaded files are actual audio/video, not arbitrary
data with a renamed extension.
"""

# Magic byte signatures for supported media formats.
# Each entry is (offset, signature_bytes, description).
# A file matches if ANY signature matches.
_SIGNATURES: list[tuple[int, bytes, str]] = [
    # WAV: RIFF....WAVE
    (0, b"RIFF", "RIFF container (WAV/AVI)"),
    # MP3: ID3v2 tag
    (0, b"ID3", "MP3 (ID3v2 tag)"),
    # MP3: sync word variants (MPEG audio frame)
    (0, b"\xff\xfb", "MP3 (MPEG1 Layer3)"),
    (0, b"\xff\xfa", "MP3 (MPEG1 Layer3)"),
    (0, b"\xff\xf3", "MP3 (MPEG2 Layer3)"),
    (0, b"\xff\xf2", "MP3 (MPEG2 Layer3)"),
    # AAC: ADTS frame sync word (0xFFF followed by 0 or 1 in the next nibble)
    (0, b"\xff\xf1", "AAC (ADTS MPEG-4)"),
    (0, b"\xff\xf9", "AAC (ADTS MPEG-2)"),
    # ISO Base Media File Format (MP4, M4A, MOV, 3GP)
    (4, b"ftyp", "ISO BMFF (MP4/M4A/MOV/3GP)"),
    # FLAC
    (0, b"fLaC", "FLAC"),
    # OGG (also covers Opus in OGG container)
    (0, b"OggS", "OGG/Opus"),
    # WebM / MKV (EBML header)
    (0, b"\x1a\x45\xdf\xa3", "EBML (WebM/MKV)"),
    # FLV (Flash Video)
    (0, b"FLV", "FLV"),
    # ASF container (WMA, WMV)
    (0, b"\x30\x26\xb2\x75\x8e\x66\xcf\x11", "ASF (WMA/WMV)"),
    # MPEG Program Stream
    (0, b"\x00\x00\x01\xba", "MPEG-PS"),
    # MPEG-1 video
    (0, b"\x00\x00\x01\xb3", "MPEG-1 video"),
    # MPEG Transport Stream (sync byte 0x47 repeated)
    (0, b"\x47", "MPEG-TS"),
]

# Minimum bytes we need to read to check all signatures
_MIN_HEADER_SIZE = 12


def is_valid_media_file(data: bytes) -> bool:
    """Check if the file data starts with a known audio/video signature.

    Args:
        data: File content (at least the first 12 bytes).

    Returns:
        True if a known media signature is found.
    """
    if len(data) < _MIN_HEADER_SIZE:
        return False

    for offset, signature, _desc in _SIGNATURES:
        end = offset + len(signature)
        if end <= len(data) and data[offset:end] == signature:
            return True

    return False
