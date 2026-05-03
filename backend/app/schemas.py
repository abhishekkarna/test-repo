from datetime import datetime

from pydantic import BaseModel, HttpUrl

from app.models import PromiseStatus, PromiseTopic, SourceType


# ── Leader ────────────────────────────────────────────────────────────────────

class LeaderBase(BaseModel):
    name: str
    party: str | None = None
    position: str | None = None
    constituency: str | None = None
    state: str | None = None
    country: str = "India"
    photo_url: str | None = None


class LeaderCreate(LeaderBase):
    pass


class LeaderOut(LeaderBase):
    id: int
    is_active: bool
    created_at: datetime
    promise_stats: dict | None = None

    class Config:
        from_attributes = True


# ── Promise ───────────────────────────────────────────────────────────────────

class PromiseBase(BaseModel):
    text: str
    summary: str
    topic: PromiseTopic = PromiseTopic.OTHER
    status: PromiseStatus = PromiseStatus.PENDING
    promised_at: datetime | None = None
    deadline: datetime | None = None
    source_url: str | None = None
    source_type: SourceType = SourceType.NEWS_ARTICLE
    source_title: str | None = None
    confidence_score: float | None = None


class PromiseCreate(PromiseBase):
    leader_id: int


class PromiseUpdate(BaseModel):
    status: PromiseStatus | None = None
    resolved_at: datetime | None = None
    note: str | None = None
    source_url: str | None = None


class StatusUpdateOut(BaseModel):
    id: int
    old_status: PromiseStatus | None
    new_status: PromiseStatus
    note: str | None
    source_url: str | None
    updated_at: datetime

    class Config:
        from_attributes = True


class ContradictionOut(BaseModel):
    id: int
    original_promise_id: int
    contradicting_promise_id: int
    explanation: str
    severity: str
    confidence_score: float | None
    verified: bool
    detected_at: datetime

    class Config:
        from_attributes = True


class PromiseOut(PromiseBase):
    id: int
    leader_id: int
    resolved_at: datetime | None = None
    verified: bool
    created_at: datetime
    updated_at: datetime
    status_updates: list[StatusUpdateOut] = []
    contradictions: list[ContradictionOut] = []

    class Config:
        from_attributes = True


# ── Ingestion ─────────────────────────────────────────────────────────────────

class IngestRequest(BaseModel):
    leader_id: int
    source_type: str  # "news" | "lok_sabha" | "rajya_sabha"
    query: str | None = None    # optional search term override
    max_articles: int = 20


class IngestResponse(BaseModel):
    job_id: int
    status: str
    promises_extracted: int
    contradictions_found: int
    message: str
