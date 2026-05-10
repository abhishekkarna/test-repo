"""Add flags table for actionable alerts on promises

Revision ID: 004
Revises: 003
Create Date: 2026-05-10 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "flags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("promise_id", sa.Integer(), sa.ForeignKey("promises.id"), nullable=False),
        sa.Column("leader_id", sa.Integer(), sa.ForeignKey("leaders.id"), nullable=False),
        sa.Column(
            "flag_type",
            sa.Enum(
                "broken_promise",
                "contradiction",
                "deadline_overdue",
                "near_duplicate_linked",
                "unfulfilled_high_confidence",
                name="flagtype",
            ),
            nullable=False,
        ),
        sa.Column(
            "severity",
            sa.Enum("low", "medium", "high", "critical", name="flagseverity"),
            nullable=False,
        ),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("related_promise_id", sa.Integer(), sa.ForeignKey("promises.id"), nullable=True),
        sa.Column("auto_flagged", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("reviewed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_flags_leader", "flags", ["leader_id"])
    op.create_index("ix_flags_type_severity", "flags", ["flag_type", "severity"])
    op.create_index("ix_flags_reviewed", "flags", ["reviewed"])


def downgrade() -> None:
    op.drop_index("ix_flags_reviewed", table_name="flags")
    op.drop_index("ix_flags_type_severity", table_name="flags")
    op.drop_index("ix_flags_leader", table_name="flags")
    op.drop_table("flags")
    op.execute("DROP TYPE IF EXISTS flagtype")
    op.execute("DROP TYPE IF EXISTS flagseverity")
