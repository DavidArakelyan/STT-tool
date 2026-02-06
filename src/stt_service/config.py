"""Configuration management for STT Service."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Database configuration."""

    model_config = SettingsConfigDict(env_prefix="DB_")

    host: str = "localhost"
    port: int = 5432
    user: str = "stt_user"
    password: str = "stt_password"
    name: str = "stt_db"
    pool_size: int = 10
    max_overflow: int = 20

    @property
    def url(self) -> str:
        """Get async database URL."""
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"

    @property
    def sync_url(self) -> str:
        """Get sync database URL for migrations."""
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


class RedisSettings(BaseSettings):
    """Redis configuration."""

    model_config = SettingsConfigDict(env_prefix="REDIS_")

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: str | None = None

    @property
    def url(self) -> str:
        """Get Redis URL."""
        if self.password:
            return f"redis://:{self.password}@{self.host}:{self.port}/{self.db}"
        return f"redis://{self.host}:{self.port}/{self.db}"


class S3Settings(BaseSettings):
    """S3/MinIO configuration."""

    model_config = SettingsConfigDict(env_prefix="S3_")

    endpoint_url: str | None = None  # None for AWS S3, set for MinIO
    access_key_id: str = ""
    secret_access_key: str = ""
    bucket_name: str = "stt-files"
    region: str = "us-east-1"

    # Presigned URL expiration (seconds)
    presigned_url_expiration: int = 3600


class CelerySettings(BaseSettings):
    """Celery configuration."""

    model_config = SettingsConfigDict(env_prefix="CELERY_")

    broker_url: str = "redis://localhost:6379/0"
    result_backend: str = "redis://localhost:6379/1"
    task_serializer: str = "json"
    result_serializer: str = "json"
    accept_content: list[str] = ["json"]
    timezone: str = "UTC"
    task_track_started: bool = True
    task_time_limit: int = 3600  # 1 hour max per task
    worker_prefetch_multiplier: int = 1
    worker_concurrency: int = 4


class ProviderSettings(BaseSettings):
    """STT Provider API keys and configuration."""

    model_config = SettingsConfigDict(env_prefix="PROVIDER_")

    # Google Gemini
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3-flash"
    gemini_rpm_limit: int = 60  # requests per minute
    gemini_max_output_tokens: int = 16384  # Configurable token limit (increased from 8192)
    gemini_request_timeout: int = 180  # API timeout in seconds
    gemini_temperature: float = 1.0  # Temperature for generation (Gemini 3 optimized for 1.0)

    # ElevenLabs
    elevenlabs_api_key: str = ""
    elevenlabs_rpm_limit: int = 100

    # OpenAI Whisper
    openai_api_key: str = ""
    openai_model: str = "whisper-1"
    openai_rpm_limit: int = 50

    # AssemblyAI
    assemblyai_api_key: str = ""
    assemblyai_rpm_limit: int = 100

    # Deepgram
    deepgram_api_key: str = ""
    deepgram_rpm_limit: int = 100

    # HiSpeech (Armenian-optimized)
    hispeech_api_key: str = ""
    hispeech_api_url: str = "https://api.hispeech.ai"
    hispeech_rpm_limit: int = 60


class ChunkingSettings(BaseSettings):
    """Audio chunking configuration."""

    model_config = SettingsConfigDict(env_prefix="CHUNKING_")

    # Maximum chunk duration in seconds
    max_chunk_duration: int = 600  # 10 minutes

    # Overlap settings for context-aware stitching
    overlap_enabled: bool = True  # Enable overlapping chunks for better stitching
    overlap_duration: float = 3.0  # 3 seconds overlap between chunks
    overlap_similarity_threshold: float = 0.8  # Similarity threshold for deduplication (increased from 0.7)

    # Context injection settings
    context_segments: int = 3  # Number of previous segments to pass as context

    # Silence detection for smart splitting
    min_silence_duration: float = 0.5  # seconds
    silence_threshold_db: int = -40  # dB

    # Maximum file size for single-chunk processing (bytes)
    max_single_chunk_size: int = 25 * 1024 * 1024  # 25MB


class RetrySettings(BaseSettings):
    """Retry and backoff configuration."""

    model_config = SettingsConfigDict(env_prefix="RETRY_")

    max_retries: int = 5
    base_delay: float = 1.0  # seconds
    max_delay: float = 60.0  # seconds
    exponential_base: float = 2.0
    jitter_max: float = 1.0  # seconds


class Settings(BaseSettings):
    """Main application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    app_name: str = "STT Service"
    app_version: str = "0.1.0"
    debug: bool = False
    environment: Literal["development", "staging", "production"] = "development"

    # API settings
    api_prefix: str = "/api/v1"
    api_keys: str = ""  # Comma-separated API keys

    # CORS
    cors_origins: list[str] = ["*"]
    cors_allow_credentials: bool = True

    # Logging
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "console"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # File upload limits
    max_upload_size: int = 500 * 1024 * 1024  # 500MB

    # Supported audio formats
    supported_audio_formats: list[str] = [
        "mp3", "wav", "m4a", "flac", "ogg", "webm", "aac", "wma", "opus"
    ]

    # Sub-configs
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    s3: S3Settings = Field(default_factory=S3Settings)
    celery: CelerySettings = Field(default_factory=CelerySettings)
    providers: ProviderSettings = Field(default_factory=ProviderSettings)
    chunking: ChunkingSettings = Field(default_factory=ChunkingSettings)
    retry: RetrySettings = Field(default_factory=RetrySettings)

    @property
    def api_keys_list(self) -> list[str]:
        """Parse API keys from comma-separated string."""
        if not self.api_keys:
            return []
        return [k.strip() for k in self.api_keys.split(",") if k.strip()]


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
