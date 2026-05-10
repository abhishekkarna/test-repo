"""
Flagging pipeline: runs after extraction to raise actionable alerts.

Five flag types are raised automatically:

  broken_promise           — status set to BROKEN
  contradiction            — a high-confidence (>= 0.7) contradiction pair exists
  deadline_overdue         — deadline passed, status still PENDING or IN_PROGRESS
  near_duplicate_linked    — promise was linked to a near-duplicate (informational)
  unfulfilled_high_confidence — high confidence (>= 0.85) promise still PENDING
                               and promised more than 2 years ago

Flags are idempotent: the same (promise_id, flag_type) will not be raised twice.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models import (
    Contradiction,
    Flag,
    FlagSeverity,
    FlagType,
    Promise,
    PromiseStatus,
)

logger = logging.getLogger(__name__)

# Minimum contradiction confidence to raise a flag
_CONTRADICTION_CONFIDENCE_MIN = 0.70
# Age (days) after which a high-confidence pending promise is flagged
_STALE_PENDING_DAYS = 730


def run_flagging_pipeline(promise_ids: list[int], db: Session) -> int:
    """
    Raise flags for the given promise IDs. Returns number of new flags created.
    """
    if not promise_ids:
        return 0

    promises = db.query(Promise).filter(Promise.id.in_(promise_ids)).all()
    total = 0
    now = datetime.now(timezone.utc)

    for promise in promises:
        total += _flag_broken(promise, db)
        total += _flag_contradiction(promise, db)
        total += _flag_deadline_overdue(promise, now, db)
        total += _flag_near_duplicate(promise, db)
        total += _flag_stale_pending(promise, now, db)

    db.commit()
    logger.info("Flagging pipeline: raised %d new flags for %d promises", total, len(promises))
    return total


def run_full_flagging_scan(leader_id: int, db: Session) -> int:
    """Scan all promises for a leader and raise any missing flags. Used on-demand."""
    promise_ids = [
        row[0]
        for row in db.query(Promise.id).filter(Promise.leader_id == leader_id).all()
    ]
    return run_flagging_pipeline(promise_ids, db)


# ── Individual flag raisers ────────────────────────────────────────────────────

def _flag_broken(promise: Promise, db: Session) -> int:
    if promise.status != PromiseStatus.BROKEN:
        return 0
    return _upsert_flag(
        db=db,
        promise=promise,
        flag_type=FlagType.BROKEN_PROMISE,
        severity=FlagSeverity.HIGH,
        message=(
            f"Promise explicitly broken: \"{promise.summary[:120]}\""
        ),
    )


def _flag_contradiction(promise: Promise, db: Session) -> int:
    contradictions = (
        db.query(Contradiction)
        .filter(
            and_(
                Contradiction.original_promise_id == promise.id,
                Contradiction.confidence_score >= _CONTRADICTION_CONFIDENCE_MIN,
            )
        )
        .all()
    )
    created = 0
    for c in contradictions:
        severity = _contradiction_severity(c.severity, c.confidence_score or 0)
        created += _upsert_flag(
            db=db,
            promise=promise,
            flag_type=FlagType.CONTRADICTION,
            severity=severity,
            message=(
                f"Contradiction detected (confidence {c.confidence_score:.2f}, "
                f"severity {c.severity}): {c.explanation[:200]}"
            ),
            related_promise_id=c.contradicting_promise_id,
        )
    return created


def _flag_deadline_overdue(promise: Promise, now: datetime, db: Session) -> int:
    if promise.deadline is None:
        return 0
    if promise.status not in (PromiseStatus.PENDING, PromiseStatus.IN_PROGRESS):
        return 0
    deadline = promise.deadline
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    if deadline >= now:
        return 0
    days_overdue = (now - deadline).days
    severity = FlagSeverity.CRITICAL if days_overdue > 365 else FlagSeverity.HIGH
    return _upsert_flag(
        db=db,
        promise=promise,
        flag_type=FlagType.DEADLINE_OVERDUE,
        severity=severity,
        message=(
            f"Deadline passed {days_overdue} day(s) ago with no resolution: "
            f"\"{promise.summary[:120]}\""
        ),
    )


def _flag_near_duplicate(promise: Promise, db: Session) -> int:
    if promise.near_duplicate_of is None:
        return 0
    return _upsert_flag(
        db=db,
        promise=promise,
        flag_type=FlagType.NEAR_DUPLICATE_LINKED,
        severity=FlagSeverity.LOW,
        message=(
            f"Promise is a near-duplicate of promise #{promise.near_duplicate_of} "
            f"(same commitment found in multiple sources)."
        ),
        related_promise_id=promise.near_duplicate_of,
    )


def _flag_stale_pending(promise: Promise, now: datetime, db: Session) -> int:
    if promise.status not in (PromiseStatus.PENDING, PromiseStatus.IN_PROGRESS):
        return 0
    if (promise.confidence_score or 0) < 0.85:
        return 0
    if promise.promised_at is None:
        return 0
    promised = promise.promised_at
    if promised.tzinfo is None:
        promised = promised.replace(tzinfo=timezone.utc)
    age_days = (now - promised).days
    if age_days < _STALE_PENDING_DAYS:
        return 0
    return _upsert_flag(
        db=db,
        promise=promise,
        flag_type=FlagType.UNFULFILLED_HIGH_CONFIDENCE,
        severity=FlagSeverity.MEDIUM,
        message=(
            f"High-confidence promise (score {promise.confidence_score:.2f}) "
            f"still unfulfilled after {age_days} days: \"{promise.summary[:120]}\""
        ),
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _upsert_flag(
    db: Session,
    promise: Promise,
    flag_type: FlagType,
    severity: FlagSeverity,
    message: str,
    related_promise_id: int | None = None,
) -> int:
    """Create flag only if one of the same type doesn't already exist for this promise."""
    existing = (
        db.query(Flag)
        .filter_by(promise_id=promise.id, flag_type=flag_type)
        .first()
    )
    if existing:
        return 0
    db.add(Flag(
        promise_id=promise.id,
        leader_id=promise.leader_id,
        flag_type=flag_type,
        severity=severity,
        message=message,
        related_promise_id=related_promise_id,
        auto_flagged=True,
        reviewed=False,
    ))
    return 1


def _contradiction_severity(raw_severity: str, confidence: float) -> FlagSeverity:
    if raw_severity == "major" or confidence >= 0.90:
        return FlagSeverity.CRITICAL
    if raw_severity == "moderate" or confidence >= 0.75:
        return FlagSeverity.HIGH
    return FlagSeverity.MEDIUM
