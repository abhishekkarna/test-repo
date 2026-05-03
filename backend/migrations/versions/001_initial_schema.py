"""initial schema

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "leaders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("party", sa.String(100), nullable=True),
        sa.Column("position", sa.String(200), nullable=True),
        sa.Column("constituency", sa.String(200), nullable=True),
        sa.Column("state", sa.String(100), nullable=True),
        sa.Column("country", sa.String(100), nullable=False, server_default="India"),
        sa.Column("photo_url", sa.String(500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_ingested_at", sa.DateTime(), nullable=True),
        sa.Column("backfill_completed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "promises",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("leader_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("topic", sa.Enum(
            "economy", "healthcare", "education", "infrastructure", "agriculture",
            "defense", "environment", "social_welfare", "governance", "foreign_policy", "other",
            name="promisetopic",
        ), nullable=False),
        sa.Column("status", sa.Enum(
            "pending", "in_progress", "fulfilled", "broken", "expired", "contradicted",
            name="promisestatus",
        ), nullable=False),
        sa.Column("promised_at", sa.DateTime(), nullable=True),
        sa.Column("deadline", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("source_url", sa.String(1000), nullable=True),
        sa.Column("source_type", sa.Enum(
            "news_article", "lok_sabha", "rajya_sabha", "press_release",
            "social_media", "speech", "manifesto",
            name="sourcetype",
        ), nullable=False),
        sa.Column("source_title", sa.String(500), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("extraction_notes", sa.Text(), nullable=True),
        sa.Column("raw_context", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("near_duplicate_of", sa.Integer(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["leader_id"], ["leaders.id"]),
        sa.ForeignKeyConstraint(["near_duplicate_of"], ["promises.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_promises_leader_topic", "promises", ["leader_id", "topic"])
    op.create_index("ix_promises_status", "promises", ["status"])
    op.create_index("ix_promises_deadline", "promises", ["deadline"])

    op.create_table(
        "promise_status_updates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("promise_id", sa.Integer(), nullable=False),
        sa.Column("old_status", sa.Enum(
            "pending", "in_progress", "fulfilled", "broken", "expired", "contradicted",
            name="promisestatus",
        ), nullable=True),
        sa.Column("new_status", sa.Enum(
            "pending", "in_progress", "fulfilled", "broken", "expired", "contradicted",
            name="promisestatus",
        ), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source_url", sa.String(1000), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["promise_id"], ["promises.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "contradictions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("original_promise_id", sa.Integer(), nullable=False),
        sa.Column("contradicting_promise_id", sa.Integer(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False, server_default="moderate"),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("detected_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["original_promise_id"], ["promises.id"]),
        sa.ForeignKeyConstraint(["contradicting_promise_id"], ["promises.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("original_promise_id", "contradicting_promise_id", name="uq_contradiction_pair"),
    )

    op.create_table(
        "scraped_articles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("url_hash", sa.String(64), nullable=False),
        sa.Column("url", sa.String(1000), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=True),
        sa.Column("leader_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("promises_extracted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scraped_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["leader_id"], ["leaders.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url_hash"),
        sa.UniqueConstraint("leader_id", "content_hash", name="uq_leader_content"),
    )
    op.create_index("ix_scraped_articles_url_hash", "scraped_articles", ["url_hash"])

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=True),
        sa.Column("mode", sa.Enum("backfill", "incremental", name="ingestionmode"), nullable=False),
        sa.Column("leader_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("promises_extracted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("contradictions_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("articles_scraped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("articles_deduped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["leader_id"], ["leaders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("ingestion_jobs")
    op.drop_index("ix_scraped_articles_url_hash", "scraped_articles")
    op.drop_table("scraped_articles")
    op.drop_table("contradictions")
    op.drop_table("promise_status_updates")
    op.drop_index("ix_promises_deadline", "promises")
    op.drop_index("ix_promises_status", "promises")
    op.drop_index("ix_promises_leader_topic", "promises")
    op.drop_table("promises")
    op.drop_table("leaders")
