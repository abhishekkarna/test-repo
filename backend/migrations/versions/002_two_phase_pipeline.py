"""two-phase pipeline: store article text and add processed/phase columns

Revision ID: 002
Revises: 001
Create Date: 2024-01-02 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scraped_articles", sa.Column("text", sa.Text(), nullable=True))
    op.add_column("scraped_articles", sa.Column("published_at", sa.String(100), nullable=True))
    op.add_column("scraped_articles", sa.Column("processed", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("ingestion_jobs", sa.Column("phase", sa.String(20), nullable=False, server_default="full"))


def downgrade() -> None:
    op.drop_column("ingestion_jobs", "phase")
    op.drop_column("scraped_articles", "processed")
    op.drop_column("scraped_articles", "published_at")
    op.drop_column("scraped_articles", "text")
