"""SQLAlchemy models for STT Service."""

import enum
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all models."""

    pass


class JobStatus(str, enum.Enum):
    """Job status enumeration."""

    PENDING = "pending"
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ChunkStatus(str, enum.Enum):
    """Chunk status enumeration."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class UserRole(str, enum.Enum):
    """User role enumeration."""

    ADMIN = "admin"
    USER = "user"


class User(Base):
    """User model for authentication and authorization."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole),
        default=UserRole.USER,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    projects: Mapped[list["Project"]] = relationship(
        "Project",
        back_populates="owner",
        order_by="Project.updated_at.desc()",
    )

    def __repr__(self) -> str:
        return f"<User {self.id} username={self.username} role={self.role}>"


class Project(Base):
    """Project model for grouping transcription jobs."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    # Owner
    user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    owner: Mapped["User | None"] = relationship("User", back_populates="projects")
    jobs: Mapped[list["Job"]] = relationship(
        "Job",
        back_populates="project",
        order_by="Job.created_at.desc()",
    )

    def __repr__(self) -> str:
        return f"<Project {self.id} name={self.name}>"


class Job(Base):
    """Transcription job model."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus),
        default=JobStatus.PENDING,
        nullable=False,
    )

    # Configuration (stored as JSON)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    # File info
    original_filename: Mapped[str | None] = mapped_column(String(500))
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    audio_format: Mapped[str | None] = mapped_column(String(50))

    # S3 references
    s3_original_key: Mapped[str | None] = mapped_column(String(500))
    s3_result_key: Mapped[str | None] = mapped_column(String(500))

    # Project association
    project_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Provider info
    provider: Mapped[str | None] = mapped_column(String(50))

    # Progress tracking
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    completed_chunks: Mapped[int] = mapped_column(Integer, default=0)

    # Results
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(50))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Webhook
    webhook_url: Mapped[str | None] = mapped_column(String(1000))
    webhook_sent: Mapped[bool] = mapped_column(Boolean, default=False)

    # Relationships
    project: Mapped["Project | None"] = relationship("Project", back_populates="jobs")
    chunks: Mapped[list["Chunk"]] = relationship(
        "Chunk",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="Chunk.chunk_index",
    )

    __table_args__ = (
        Index("idx_jobs_status", "status"),
        Index("idx_jobs_created_at", "created_at"),
        Index("idx_jobs_provider", "provider"),
        Index("idx_jobs_project_id", "project_id"),
    )

    def __repr__(self) -> str:
        return f"<Job {self.id} status={self.status}>"


class Chunk(Base):
    """Audio chunk model for granular progress tracking."""

    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    job_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )

    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ChunkStatus] = mapped_column(
        Enum(ChunkStatus),
        default=ChunkStatus.PENDING,
        nullable=False,
    )

    # Chunk info
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[float] = mapped_column(Float, nullable=False)
    s3_chunk_key: Mapped[str | None] = mapped_column(String(500))

    # Processing
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)

    # Result
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    job: Mapped["Job"] = relationship("Job", back_populates="chunks")

    __table_args__ = (
        Index("idx_chunks_job_status", "job_id", "status"),
        Index("idx_chunks_job_index", "job_id", "chunk_index", unique=True),
    )

    def __repr__(self) -> str:
        return f"<Chunk {self.id} job={self.job_id} index={self.chunk_index} status={self.status}>"
