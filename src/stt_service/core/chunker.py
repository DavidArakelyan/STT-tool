"""Audio chunking with FFmpeg for large file processing."""

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import ffmpeg
import structlog

from stt_service.config import get_settings
from stt_service.utils.exceptions import ChunkingError

logger = structlog.get_logger()
settings = get_settings()


@dataclass
class ChunkInfo:
    """Information about a single audio chunk."""

    index: int
    start_time: float
    end_time: float
    duration: float
    file_path: str | None = None
    file_size: int | None = None


@dataclass
class AudioMetadata:
    """Audio file metadata."""

    duration: float  # Total duration in seconds
    format: str  # Audio format/codec
    sample_rate: int
    channels: int
    bit_rate: int | None = None
    file_size: int | None = None


class AudioChunker:
    """Smart audio chunking using FFmpeg with silence detection."""

    def __init__(
        self,
        max_chunk_duration: float | None = None,
        overlap_duration: float | None = None,
        silence_threshold_db: int | None = None,
        min_silence_duration: float | None = None,
    ) -> None:
        """Initialize chunker with configuration.

        Args:
            max_chunk_duration: Maximum chunk duration in seconds
            overlap_duration: Overlap between chunks in seconds
            silence_threshold_db: Silence detection threshold in dB
            min_silence_duration: Minimum silence duration to detect
        """
        self.max_chunk_duration = max_chunk_duration or settings.chunking.max_chunk_duration
        self.overlap_duration = overlap_duration or settings.chunking.overlap_duration
        self.silence_threshold_db = silence_threshold_db or settings.chunking.silence_threshold_db
        self.min_silence_duration = min_silence_duration or settings.chunking.min_silence_duration

    async def get_audio_metadata(self, file_path: str) -> AudioMetadata:
        """Get audio file metadata using FFprobe.

        Args:
            file_path: Path to audio file

        Returns:
            AudioMetadata object
        """
        try:
            probe = await asyncio.to_thread(
                ffmpeg.probe, file_path, v="error", show_entries="format=duration,bit_rate:stream=sample_rate,channels,codec_name"
            )

            format_info = probe.get("format", {})
            streams = probe.get("streams", [{}])
            audio_stream = next(
                (s for s in streams if s.get("codec_type") == "audio"),
                streams[0] if streams else {},
            )

            duration = float(format_info.get("duration", 0))
            file_size = os.path.getsize(file_path) if os.path.exists(file_path) else None

            return AudioMetadata(
                duration=duration,
                format=audio_stream.get("codec_name", "unknown"),
                sample_rate=int(audio_stream.get("sample_rate", 44100)),
                channels=int(audio_stream.get("channels", 2)),
                bit_rate=int(format_info.get("bit_rate", 0)) if format_info.get("bit_rate") else None,
                file_size=file_size,
            )

        except ffmpeg.Error as e:
            raise ChunkingError(f"Failed to probe audio file: {e.stderr.decode() if e.stderr else str(e)}")
        except Exception as e:
            raise ChunkingError(f"Failed to get audio metadata: {e}")

    async def detect_silence_points(
        self,
        file_path: str,
        duration: float,
    ) -> list[float]:
        """Detect silence points in audio for smart splitting.

        Args:
            file_path: Path to audio file
            duration: Total audio duration

        Returns:
            List of timestamps (in seconds) where silences occur
        """
        try:
            # Use FFmpeg silence detection filter
            cmd = (
                ffmpeg
                .input(file_path)
                .filter("silencedetect", n=f"{self.silence_threshold_db}dB", d=self.min_silence_duration)
                .output("-", format="null")
            )

            # Run FFmpeg and capture stderr (where silence info is logged)
            process = await asyncio.to_thread(
                lambda: cmd.run(capture_stdout=True, capture_stderr=True)
            )
            stderr = process[1].decode("utf-8")

            # Parse silence end timestamps
            silence_points = []
            for line in stderr.split("\n"):
                if "silence_end" in line:
                    try:
                        # Format: [silencedetect @ 0x...] silence_end: 123.456
                        parts = line.split("silence_end:")
                        if len(parts) > 1:
                            timestamp = float(parts[1].split()[0])
                            silence_points.append(timestamp)
                    except (ValueError, IndexError):
                        continue

            return silence_points

        except Exception as e:
            logger.warning("Silence detection failed, using fixed intervals", error=str(e))
            return []

    def calculate_chunk_boundaries(
        self,
        duration: float,
        silence_points: list[float] | None = None,
    ) -> list[tuple[float, float]]:
        """Calculate chunk boundaries based on duration and silence points.

        Args:
            duration: Total audio duration
            silence_points: Optional list of silence timestamps

        Returns:
            List of (start_time, end_time) tuples
        """
        if duration <= self.max_chunk_duration:
            # Single chunk for short audio
            return [(0.0, duration)]

        boundaries = []
        current_start = 0.0

        while current_start < duration:
            # Target end time for this chunk
            target_end = min(current_start + self.max_chunk_duration, duration)

            if target_end >= duration:
                # Last chunk
                boundaries.append((current_start, duration))
                break

            # Find best split point near target_end
            if silence_points:
                # Look for silence within 20% of target end
                search_start = target_end * 0.8
                search_end = target_end * 1.1

                candidates = [
                    sp for sp in silence_points
                    if search_start <= sp <= min(search_end, duration)
                ]

                if candidates:
                    # Use the silence point closest to target
                    split_point = min(candidates, key=lambda x: abs(x - target_end))
                else:
                    split_point = target_end
            else:
                split_point = target_end

            boundaries.append((current_start, split_point))

            # Next chunk starts with overlap
            current_start = max(0, split_point - self.overlap_duration)

        return boundaries

    async def chunk_audio(
        self,
        input_path: str,
        output_dir: str | None = None,
        output_format: str = "wav",
    ) -> list[ChunkInfo]:
        """Split audio file into chunks.

        Args:
            input_path: Path to input audio file
            output_dir: Directory for output chunks (uses temp dir if None)
            output_format: Output audio format

        Returns:
            List of ChunkInfo objects
        """
        # Get audio metadata
        metadata = await self.get_audio_metadata(input_path)
        logger.info(
            "Processing audio",
            duration=metadata.duration,
            format=metadata.format,
            sample_rate=metadata.sample_rate,
        )

        # Create output directory
        if output_dir is None:
            output_dir = tempfile.mkdtemp(prefix="stt_chunks_")
        else:
            os.makedirs(output_dir, exist_ok=True)

        # Detect silence points for smart splitting
        silence_points = await self.detect_silence_points(input_path, metadata.duration)
        logger.info("Detected silence points", count=len(silence_points))

        # Calculate chunk boundaries
        boundaries = self.calculate_chunk_boundaries(metadata.duration, silence_points)
        logger.info("Calculated chunks", count=len(boundaries))

        # Create chunks
        chunks = []
        for i, (start_time, end_time) in enumerate(boundaries):
            output_path = os.path.join(output_dir, f"chunk_{i:04d}.{output_format}")

            await self._extract_chunk(
                input_path,
                output_path,
                start_time,
                end_time,
                output_format,
            )

            file_size = os.path.getsize(output_path) if os.path.exists(output_path) else None

            chunks.append(
                ChunkInfo(
                    index=i,
                    start_time=start_time,
                    end_time=end_time,
                    duration=end_time - start_time,
                    file_path=output_path,
                    file_size=file_size,
                )
            )

        logger.info("Created audio chunks", count=len(chunks), output_dir=output_dir)
        return chunks

    async def _extract_chunk(
        self,
        input_path: str,
        output_path: str,
        start_time: float,
        end_time: float,
        output_format: str,
    ) -> None:
        """Extract a single chunk from audio file.

        Args:
            input_path: Source audio path
            output_path: Output chunk path
            start_time: Chunk start time
            end_time: Chunk end time
            output_format: Output format
        """
        try:
            duration = end_time - start_time

            # Build FFmpeg command
            stream = ffmpeg.input(input_path, ss=start_time, t=duration)

            # Apply audio processing
            stream = stream.output(
                output_path,
                acodec="pcm_s16le" if output_format == "wav" else "libmp3lame",
                ar=16000,  # 16kHz sample rate for STT
                ac=1,  # Mono audio
            )

            # Run FFmpeg
            await asyncio.to_thread(
                lambda: stream.overwrite_output().run(quiet=True)
            )

        except ffmpeg.Error as e:
            raise ChunkingError(
                f"Failed to extract chunk: {e.stderr.decode() if e.stderr else str(e)}"
            )

    async def convert_to_wav(
        self,
        input_path: str,
        output_path: str | None = None,
        sample_rate: int = 16000,
        channels: int = 1,
    ) -> str:
        """Convert audio to WAV format optimized for STT.

        Args:
            input_path: Input audio path
            output_path: Output path (uses temp file if None)
            sample_rate: Target sample rate
            channels: Number of channels (1=mono, 2=stereo)

        Returns:
            Path to converted WAV file
        """
        if output_path is None:
            fd, output_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)

        try:
            stream = ffmpeg.input(input_path)
            stream = stream.output(
                output_path,
                acodec="pcm_s16le",
                ar=sample_rate,
                ac=channels,
            )

            await asyncio.to_thread(
                lambda: stream.overwrite_output().run(quiet=True)
            )

            return output_path

        except ffmpeg.Error as e:
            raise ChunkingError(
                f"Failed to convert audio: {e.stderr.decode() if e.stderr else str(e)}"
            )

    @staticmethod
    def cleanup_chunks(chunks: list[ChunkInfo]) -> None:
        """Clean up temporary chunk files.

        Args:
            chunks: List of chunks to clean up
        """
        for chunk in chunks:
            if chunk.file_path and os.path.exists(chunk.file_path):
                try:
                    os.remove(chunk.file_path)
                except OSError:
                    pass

        # Try to remove the directory if empty
        if chunks and chunks[0].file_path:
            chunk_dir = os.path.dirname(chunks[0].file_path)
            try:
                os.rmdir(chunk_dir)
            except OSError:
                pass


# Singleton instance
audio_chunker = AudioChunker()
