import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PromiseStatus(str, enum.Enum):
    PENDING = "pending"          # Made but no action yet
    IN_PROGRESS = "in_progress"  # Actively being worked on
    FULFILLED = "fulfilled"      # Delivered
    BROKEN = "broken"            # Explicitly walked back or failed
    EXPIRED = "expired"          # Deadline passed with no delivery
    CONTRADICTED = "contradicted" # Contradicts an earlier promise


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


class Leader(Base):
    __tablename__ = "leaders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    party: Mapped[str | None] = mapped_column(String(100))
    position: Mapped[str | None] = mapped_column(String(200))  # e.g. "Prime Minister", "Chief Minister of Maharashtra"
    constituency: Mapped[str | None] = mapped_column(String(200))
    state: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str] = mapped_column(String(100), default="India")
    photo_url: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    promises: Mapped[list["Promise"]] = relationship("Promise", back_populates="leader")

    @property
    def promise_stats(self):
        counts = {}
        for s in PromiseStatus:
            counts[s.value] = sum(1 for p in self.promises if p.status == s)
        counts["total"] = len(self.promises)
        return counts


class Promise(Base):
    __tablename__ = "promises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leader_id: Mapped[int] = mapped_column(Integer, ForeignKey("leaders.id"), nullable=False)

    # Core promise content
    text: Mapped[str] = mapped_column(Text, nullable=False)             # Original statement
    summary: Mapped[str] = mapped_column(String(500), nullable=False)   # LLM-generated short summary
    topic: Mapped[PromiseTopic] = mapped_column(Enum(PromiseTopic), default=PromiseTopic.OTHER)
    status: Mapped[PromiseStatus] = mapped_column(Enum(PromiseStatus), default=PromiseStatus.PENDING)

    # Temporal context
    promised_at: Mapped[datetime | None] = mapped_column(DateTime)       # When the promise was made
    deadline: Mapped[datetime | None] = mapped_column(DateTime)          # Stated or inferred deadline
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)       # When status changed to fulfilled/broken

    # Source tracing
    source_url: Mapped[str | None] = mapped_column(String(1000))
    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType), default=SourceType.NEWS_ARTICLE)
    source_title: Mapped[str | None] = mapped_column(String(500))

    # LLM extraction metadata
    confidence_score: Mapped[float | None] = mapped_column(Float)        # 0–1, how confident LLM is this is a real promise
    extraction_notes: Mapped[str | None] = mapped_column(Text)           # LLM reasoning
    raw_context: Mapped[str | None] = mapped_column(Text)               # Surrounding text used for extraction
    verified: Mapped[bool] = mapped_column(Boolean, default=False)       # Human-reviewed

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
    """Audit trail for every status change on a promise."""

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
    """Links two promises that contradict each other, with LLM-generated explanation."""

    __tablename__ = "contradictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    original_promise_id: Mapped[int] = mapped_column(Integer, ForeignKey("promises.id"), nullable=False)
    contradicting_promise_id: Mapped[int] = mapped_column(Integer, ForeignKey("promises.id"), nullable=False)

    explanation: Mapped[str] = mapped_column(Text, nullable=False)   # LLM explanation of the contradiction
    severity: Mapped[str] = mapped_column(String(20), default="moderate")  # minor / moderate / major
    confidence_score: Mapped[float | None] = mapped_column(Float)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)

    detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    original_promise: Mapped["Promise"] = relationship(
        "Promise", foreign_keys=[original_promise_id], back_populates="contradictions_as_original"
    )
    contradicting_promise: Mapped["Promise"] = relationship(
        "Promise", foreign_keys=[contradicting_promise_id], back_populates="contradictions_as_new"
    )


class IngestionJob(Base):
    """Tracks each scraping / ingestion run."""

    __tablename__ = "ingestion_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_type: Mapped[str] = mapped_column(String(50))
    leader_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("leaders.id"))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/running/done/failed
    promises_extracted: Mapped[int] = mapped_column(Integer, default=0)
    contradictions_found: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
