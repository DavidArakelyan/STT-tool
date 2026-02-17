"""Add projects table and project_id to jobs.

Revision ID: 002
Revises: 001
Create Date: 2024-01-28 00:00:01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create projects table (IF NOT EXISTS for idempotency with init_db)
    op.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id UUID NOT NULL PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            description TEXT,
            total_cost_usd FLOAT DEFAULT 0.0 NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL
        )
    """)

    # Add project_id column to jobs (IF NOT EXISTS for idempotency)
    op.execute("""
        ALTER TABLE jobs ADD COLUMN IF NOT EXISTS project_id UUID
            REFERENCES projects(id) ON DELETE SET NULL
    """)

    # Add index on jobs.project_id (IF NOT EXISTS)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_jobs_project_id ON jobs (project_id)
    """)


def downgrade() -> None:
    op.drop_index("idx_jobs_project_id", table_name="jobs")
    op.drop_column("jobs", "project_id")
    op.drop_table("projects")
