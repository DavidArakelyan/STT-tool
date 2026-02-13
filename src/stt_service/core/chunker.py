"""Audio chunking with FFmpeg for large file processing."""

import asyncio
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
    ) -> None:
        """Initialize chunker with configuration.

        Args:
            max_chunk_duration: Maximum chunk duration in seconds
            overlap_duration: Overlap between chunks in seconds
        """
        self.max_chunk_duration = max_chunk_duration or settings.chunking.max_chunk_duration
        self.overlap_duration = overlap_duration or settings.chunking.overlap_duration

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

    async def extract_audio_from_video(
        self,
        video_path: str,
        output_path: str | None = None,
    ) -> str:
        """Extract audio from video file using FFmpeg.

        Args:
            video_path: Path to video file
            output_path: Optional output path (will use temp file if not provided)

        Returns:
            Path to the extracted audio file (WAV format)
        """
        try:
            # Determine output path
            if output_path is None:
                output_dir = Path(video_path).parent
                output_path = str(output_dir / f"{Path(video_path).stem}_audio.wav")

            logger.info(
                "Extracting audio from video",
                video_path=video_path,
                output_path=output_path,
            )

            # Extract audio using FFmpeg
            # -vn: no video, -acodec pcm_s16le: 16-bit PCM, -ar 16000: 16kHz sample rate
            stream = ffmpeg.input(video_path)
            stream = ffmpeg.output(
                stream,
                output_path,
                vn=None,  # No video
                acodec='pcm_s16le',  # 16-bit WAV
                ar=16000,  # 16kHz sample rate (optimal for speech)
                ac=1,  # Mono
            )
            stream = ffmpeg.overwrite_output(stream)

            await asyncio.to_thread(ffmpeg.run, stream, quiet=True)

            # Verify output file exists and has content
            if not os.path.exists(output_path):
                raise ChunkingError("Audio extraction failed: output file not created")

            output_size = os.path.getsize(output_path)
            if output_size == 0:
                raise ChunkingError("Audio extraction failed: output file is empty")

            logger.info(
                "Audio extracted successfully",
                output_path=output_path,
                output_size=output_size,
            )

            return output_path

        except ffmpeg.Error as e:
            error_msg = e.stderr.decode() if e.stderr else str(e)
            raise ChunkingError(f"Failed to extract audio from video: {error_msg}")
        except Exception as e:
            if isinstance(e, ChunkingError):
                raise
            raise ChunkingError(f"Failed to extract audio from video: {e}")

    def calculate_chunk_boundaries(
        self,
        duration: float,
    ) -> list[tuple[float, float]]:
        """Calculate fixed-duration chunk boundaries with overlap.

        Args:
            duration: Total audio duration

        Returns:
            List of (start_time, end_time) tuples
        """
        if duration <= self.max_chunk_duration:
            return [(0.0, duration)]

        boundaries = []
        current_start = 0.0

        while current_start < duration:
            chunk_end = min(current_start + self.max_chunk_duration, duration)
            boundaries.append((current_start, chunk_end))

            if chunk_end >= duration:
                break

            # Next chunk starts with overlap
            current_start = chunk_end - self.overlap_duration

        logger.info(
            "Chunk boundaries calculated",
            total_chunks=len(boundaries),
            overlap_duration=self.overlap_duration,
        )

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

        # Calculate chunk boundaries
        boundaries = self.calculate_chunk_boundaries(metadata.duration)

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
