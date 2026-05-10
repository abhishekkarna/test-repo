"""Add embedding vector column to promises for near-duplicate detection

Revision ID: 003
Revises: 002
Create Date: 2026-05-10 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("promises", sa.Column("embedding", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("promises", "embedding")
