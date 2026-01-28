"""Initial schema with jobs and chunks tables.

Revision ID: 001
Revises:
Create Date: 2024-01-27 00:00:01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create job status enum
    job_status_enum = postgresql.ENUM(
        "pending",
        "uploaded",
        "processing",
        "completed",
        "failed",
        "cancelled",
        name="jobstatus",
        create_type=True,
    )
    job_status_enum.create(op.get_bind(), checkfirst=True)

    # Create chunk status enum
    chunk_status_enum = postgresql.ENUM(
        "pending",
        "processing",
        "completed",
        "failed",
        name="chunkstatus",
        create_type=True,
    )
    chunk_status_enum.create(op.get_bind(), checkfirst=True)

    # Create jobs table
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "status",
            job_status_enum,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("config", postgresql.JSONB, nullable=False),
        sa.Column("original_filename", sa.String(500), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger, nullable=True),
        sa.Column("duration_seconds", sa.Float, nullable=True),
        sa.Column("audio_format", sa.String(50), nullable=True),
        sa.Column("s3_original_key", sa.String(500), nullable=True),
        sa.Column("s3_result_key", sa.String(500), nullable=True),
        sa.Column("provider", sa.String(50), nullable=True),
        sa.Column("total_chunks", sa.Integer, server_default="0", nullable=False),
        sa.Column("completed_chunks", sa.Integer, server_default="0", nullable=False),
        sa.Column("result", postgresql.JSONB, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("webhook_url", sa.String(1000), nullable=True),
        sa.Column("webhook_sent", sa.Boolean, server_default="false", nullable=False),
    )

    # Create indexes on jobs
    op.create_index("idx_jobs_status", "jobs", ["status"])
    op.create_index("idx_jobs_created_at", "jobs", ["created_at"])
    op.create_index("idx_jobs_provider", "jobs", ["provider"])

    # Create chunks table
    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column(
            "status",
            chunk_status_enum,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("start_time", sa.Float, nullable=False),
        sa.Column("end_time", sa.Float, nullable=False),
        sa.Column("s3_chunk_key", sa.String(500), nullable=True),
        sa.Column("attempt_count", sa.Integer, server_default="0", nullable=False),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("result", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Create indexes on chunks
    op.create_index("idx_chunks_job_status", "chunks", ["job_id", "status"])
    op.create_index(
        "idx_chunks_job_index",
        "chunks",
        ["job_id", "chunk_index"],
        unique=True,
    )


def downgrade() -> None:
    # Drop chunks table
    op.drop_index("idx_chunks_job_index", table_name="chunks")
    op.drop_index("idx_chunks_job_status", table_name="chunks")
    op.drop_table("chunks")

    # Drop jobs table
    op.drop_index("idx_jobs_provider", table_name="jobs")
    op.drop_index("idx_jobs_created_at", table_name="jobs")
    op.drop_index("idx_jobs_status", table_name="jobs")
    op.drop_table("jobs")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS chunkstatus")
    op.execute("DROP TYPE IF EXISTS jobstatus")
