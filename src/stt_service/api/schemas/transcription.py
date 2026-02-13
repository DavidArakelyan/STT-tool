"""API schemas for transcription requests and responses."""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ProviderType(str, Enum):
    """Supported STT providers."""

    GEMINI = "gemini"
    ELEVENLABS = "elevenlabs"
    WHISPER = "whisper"
    ASSEMBLYAI = "assemblyai"
    DEEPGRAM = "deepgram"
    HISPEECH = "hispeech"
    WAV = "wav"


class RecordingType(str, Enum):
    """Types of recordings."""

    MEETING = "meeting"
    INTERVIEW = "interview"
    PODCAST = "podcast"
    CALL = "call"
    LECTURE = "lecture"
    OTHER = "other"


class SpeakerHint(BaseModel):
    """Hint about an expected speaker."""

    name: str
    role: str | None = None
    language: str | None = None  # Primary language for this speaker


class TranscriptionContext(BaseModel):
    """Contextual information to improve transcription quality."""

    recording_type: RecordingType = RecordingType.OTHER

    # Domain/topic hints
    domain: str | None = Field(
        None,
        description="Domain for specialized vocabulary (e.g., 'medical', 'legal', 'technical')",
    )

    # Custom vocabulary/terms (especially for Armenian transliterations)
    custom_vocabulary: list[str] = Field(
        default_factory=list,
        description="Custom terms to help recognition (e.g., ['API', 'backend', 'deployment'])",
    )

    # Speaker information if known
    expected_speakers: list[SpeakerHint] = Field(
        default_factory=list,
        description="Information about expected speakers",
    )

    # Free-form prompt for additional context
    prompt: str | None = Field(
        None,
        description="Free-form context about the recording",
    )


class DiarizationConfig(BaseModel):
    """Speaker diarization settings."""

    enabled: bool = True
    min_speakers: int | None = Field(None, ge=1, le=20)
    max_speakers: int | None = Field(None, ge=1, le=20)
    speaker_labels: dict[str, str] = Field(
        default_factory=dict,
        description="Map speaker IDs to names (e.g., {'SPEAKER_00': 'John'})",
    )


class OutputConfig(BaseModel):
    """Output formatting configuration."""

    include_timestamps: bool = True
    timestamp_granularity: Literal["segment", "word"] = "segment"
    include_confidence: bool = False
    paragraph_detection: bool = True
    punctuation: bool = True


class TranscriptionRequest(BaseModel):
    """Configuration for a transcription job."""

    # Provider selection
    provider: ProviderType = ProviderType.GEMINI

    # Language configuration
    language: str = Field(
        "hy",
        description="Primary language (ISO 639-1 code). 'hy' for Armenian.",
    )
    additional_languages: list[str] = Field(
        default_factory=lambda: ["en", "ru"],
        description="Additional languages that may appear (for code-switching)",
    )

    # Context/Prompt for better accuracy
    context: TranscriptionContext | None = None

    # Diarization settings
    diarization: DiarizationConfig = Field(default_factory=DiarizationConfig)

    # Output configuration
    output: OutputConfig = Field(default_factory=OutputConfig)

    # Webhook URL for completion notification
    webhook_url: str | None = Field(
        None,
        description="URL to POST results when transcription completes",
    )


class TranscriptionUrlRequest(TranscriptionRequest):
    """Request for transcription via URL."""

    audio_url: str = Field(..., description="URL of the audio file to transcribe")


# Response schemas


class Word(BaseModel):
    """Word-level transcription detail."""

    text: str
    start_time: float
    end_time: float
    confidence: float | None = None


class TranscriptSegment(BaseModel):
    """A single segment of transcribed speech."""

    speaker_id: str
    speaker_label: str | None = None

    start_time: float  # seconds
    end_time: float  # seconds

    text: str

    # Optional word-level details
    words: list[Word] | None = None

    confidence: float | None = None


class Speaker(BaseModel):
    """Speaker summary."""

    speaker_id: str
    label: str | None = None
    total_duration: float  # Total speaking time in seconds
    segment_count: int


class Transcript(BaseModel):
    """Structured transcript with diarization."""

    # Full text without speaker labels
    text: str

    # Segments with speaker labels and timestamps
    segments: list[TranscriptSegment]

    # Speaker summary
    speakers: list[Speaker]


class JobStatus(str, Enum):
    """Job status values."""

    PENDING = "pending"
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ChunkProgress(BaseModel):
    """Progress information for a single chunk."""

    chunk_index: int
    status: str
    start_time: float
    end_time: float
    attempt_count: int
    error: str | None = None


class JobProgress(BaseModel):
    """Detailed job progress information."""

    job_id: str
    status: JobStatus
    total_chunks: int
    completed_chunks: int
    failed_chunks: int
    progress_percent: float
    chunks: list[ChunkProgress] | None = None


class JobResponse(BaseModel):
    """Response for job status queries."""

    job_id: str
    status: JobStatus

    # File info
    original_filename: str | None = None
    file_size_bytes: int | None = None
    duration_seconds: float | None = None

    # Provider
    provider: str | None = None

    # Progress
    total_chunks: int = 0
    completed_chunks: int = 0

    # Timestamps
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None

    # Error if failed
    error_message: str | None = None


class TranscriptionResult(BaseModel):
    """Complete transcription result."""

    job_id: str
    status: JobStatus

    # Metadata
    duration_seconds: float
    original_filename: str | None = None
    language_detected: str | None = None
    provider_used: str

    # Main transcript
    transcript: Transcript

    # Processing info
    processing_time_seconds: float
    chunks_processed: int

    # Warnings/notes
    warnings: list[str] = Field(default_factory=list)


class JobListResponse(BaseModel):
    """Response for listing jobs."""

    jobs: list[JobResponse]
    total: int
    limit: int
    offset: int


class TranscriptionSubmitResponse(BaseModel):
    """Response when submitting a new transcription job."""

    job_id: str
    status: JobStatus
    message: str
