import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PromiseStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    FULFILLED = "fulfilled"
    BROKEN = "broken"
    EXPIRED = "expired"
    CONTRADICTED = "contradicted"


class PromiseTopic(str, enum.Enum):
    ECONOMY = "economy"
    HEALTHCARE = "healthcare"
    EDUCATION = "education"
    INFRASTRUCTURE = "infrastructure"
    AGRICULTURE = "agriculture"
    DEFENSE = "defense"
    ENVIRONMENT = "environment"
    SOCIAL_WELFARE = "social_welfare"
    GOVERNANCE = "governance"
    FOREIGN_POLICY = "foreign_policy"
    OTHER = "other"


class SourceType(str, enum.Enum):
    NEWS_ARTICLE = "news_article"
    LOK_SABHA = "lok_sabha"
    RAJYA_SABHA = "rajya_sabha"
    PRESS_RELEASE = "press_release"
    SOCIAL_MEDIA = "social_media"
    SPEECH = "speech"
    MANIFESTO = "manifesto"


class IngestionMode(str, enum.Enum):
    BACKFILL = "backfill"        # one-time historical load, no date filter
    INCREMENTAL = "incremental"  # daily scan, only since last_ingested_at


class Leader(Base):
    __tablename__ = "leaders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    party: Mapped[str | None] = mapped_column(String(100))
    position: Mapped[str | None] = mapped_column(String(200))
    constituency: Mapped[str | None] = mapped_column(String(200))
    state: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str] = mapped_column(String(100), default="India")
    photo_url: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_ingested_at: Mapped[datetime | None] = mapped_column(DateTime)  # cutoff for incremental runs
    backfill_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    promises: Mapped[list["Promise"]] = relationship("Promise", back_populates="leader")

    @property
    def promise_stats(self):
        counts = {s.value: sum(1 for p in self.promises if p.status == s) for s in PromiseStatus}
        counts["total"] = len(self.promises)
        return counts


class Promise(Base):
    __tablename__ = "promises"
    __table_args__ = (
        Index("ix_promises_leader_topic", "leader_id", "topic"),
        Index("ix_promises_status", "status"),
        Index("ix_promises_deadline", "deadline"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leader_id: Mapped[int] = mapped_column(Integer, ForeignKey("leaders.id"), nullable=False)

    text: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    topic: Mapped[PromiseTopic] = mapped_column(Enum(PromiseTopic), default=PromiseTopic.OTHER)
    status: Mapped[PromiseStatus] = mapped_column(Enum(PromiseStatus), default=PromiseStatus.PENDING)

    promised_at: Mapped[datetime | None] = mapped_column(DateTime)
    deadline: Mapped[datetime | None] = mapped_column(DateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)

    source_url: Mapped[str | None] = mapped_column(String(1000))
    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType), default=SourceType.NEWS_ARTICLE)
    source_title: Mapped[str | None] = mapped_column(String(500))

    confidence_score: Mapped[float | None] = mapped_column(Float)
    extraction_notes: Mapped[str | None] = mapped_column(Text)
    raw_context: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64))     # SHA-256 of summary for promise dedup
    near_duplicate_of: Mapped[int | None] = mapped_column(Integer, ForeignKey("promises.id"))  # future embedding dedup
    verified: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    leader: Mapped["Leader"] = relationship("Leader", back_populates="promises")
    status_updates: Mapped[list["PromiseStatusUpdate"]] = relationship("PromiseStatusUpdate", back_populates="promise")
    contradictions_as_original: Mapped[list["Contradiction"]] = relationship(
        "Contradiction", foreign_keys="Contradiction.original_promise_id", back_populates="original_promise"
    )
    contradictions_as_new: Mapped[list["Contradiction"]] = relationship(
        "Contradiction", foreign_keys="Contradiction.contradicting_promise_id", back_populates="contradicting_promise"
    )


class PromiseStatusUpdate(Base):
    __tablename__ = "promise_status_updates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    promise_id: Mapped[int] = mapped_column(Integer, ForeignKey("promises.id"), nullable=False)
    old_status: Mapped[PromiseStatus | None] = mapped_column(Enum(PromiseStatus))
    new_status: Mapped[PromiseStatus] = mapped_column(Enum(PromiseStatus), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(1000))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    promise: Mapped["Promise"] = relationship("Promise", back_populates="status_updates")


class Contradiction(Base):
    __tablename__ = "contradictions"
    __table_args__ = (
        UniqueConstraint("original_promise_id", "contradicting_promise_id", name="uq_contradiction_pair"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    original_promise_id: Mapped[int] = mapped_column(Integer, ForeignKey("promises.id"), nullable=False)
    contradicting_promise_id: Mapped[int] = mapped_column(Integer, ForeignKey("promises.id"), nullable=False)

    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), default="moderate")
    confidence_score: Mapped[float | None] = mapped_column(Float)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    original_promise: Mapped["Promise"] = relationship(
        "Promise", foreign_keys=[original_promise_id], back_populates="contradictions_as_original"
    )
    contradicting_promise: Mapped["Promise"] = relationship(
        "Promise", foreign_keys=[contradicting_promise_id], back_populates="contradictions_as_new"
    )


class ScrapedArticle(Base):
    """Deduplication cache — prevents re-processing the same article."""

    __tablename__ = "scraped_articles"
    __table_args__ = (
        UniqueConstraint("leader_id", "content_hash", name="uq_leader_content"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50))
    leader_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("leaders.id"))
    title: Mapped[str | None] = mapped_column(String(500))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    promises_extracted: Mapped[int] = mapped_column(Integer, default=0)
    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_type: Mapped[str] = mapped_column(String(50))
    mode: Mapped[IngestionMode] = mapped_column(Enum(IngestionMode), default=IngestionMode.INCREMENTAL)
    leader_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("leaders.id"))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/running/done/failed
    promises_extracted: Mapped[int] = mapped_column(Integer, default=0)
    contradictions_found: Mapped[int] = mapped_column(Integer, default=0)
    articles_scraped: Mapped[int] = mapped_column(Integer, default=0)
    articles_deduped: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
