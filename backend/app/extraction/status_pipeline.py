"""
Status update pipeline.

Two responsibilities:
1. run_status_update_pipeline — check fresh articles against existing PENDING/IN_PROGRESS
   promises for a leader and update their status via LLM.
2. check_expired_promises — no LLM needed; bulk-expire promises whose deadline has passed.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.extraction.extractor import check_promise_status
from app.models import Promise, PromiseStatus, PromiseStatusUpdate

logger = logging.getLogger(__name__)


def run_status_update_pipeline(
    leader_id: int,
    articles: list[dict],
    db: Session,
) -> int:
    """
    For each fresh article, check it against active promises for this leader.
    Returns the number of promises whose status was updated.
    """
    active_promises = (
        db.query(Promise)
        .filter(
            Promise.leader_id == leader_id,
            Promise.status.in_([PromiseStatus.PENDING, PromiseStatus.IN_PROGRESS]),
        )
        .all()
    )
    if not active_promises:
        return 0

    # Load the leader name once
    from app.models import Leader
    leader = db.get(Leader, leader_id)
    leader_name = leader.name if leader else "Unknown"

    total_updated = 0

    for article in articles:
        article_title = article.get("title") or ""
        article_text = article.get("text") or ""
        article_date = article.get("published_at")

        if not article_text.strip():
            continue

        # Batch active promises into groups to stay within LLM context
        for batch_start in range(0, len(active_promises), settings.status_check_batch_size):
            batch = active_promises[batch_start: batch_start + settings.status_check_batch_size]

            batch_dicts = [
                {
                    "id": p.id,
                    "summary": p.summary,
                    "topic": p.topic.value,
                    "status": p.status.value,
                    "promised_at": str(p.promised_at) if p.promised_at else None,
                }
                for p in batch
            ]

            results = check_promise_status(
                article_title=article_title,
                article_text=article_text,
                article_date=article_date,
                leader_name=leader_name,
                existing_promises=batch_dicts,
            )

            for update in results:
                promise_id = update.get("promise_id")
                new_status_str = update.get("new_status")
                confidence = update.get("confidence_score") or 0.0

                if not promise_id or not new_status_str or confidence < 0.7:
                    continue

                try:
                    new_status = PromiseStatus(new_status_str)
                except ValueError:
                    logger.warning("Unknown status '%s' from LLM", new_status_str)
                    continue

                promise = db.get(Promise, promise_id)
                if not promise or promise.status == new_status:
                    continue

                old_status = promise.status
                promise.status = new_status

                if new_status in (PromiseStatus.FULFILLED, PromiseStatus.BROKEN, PromiseStatus.EXPIRED):
                    promise.resolved_at = _parse_date(article_date) or datetime.now(timezone.utc)

                status_update = PromiseStatusUpdate(
                    promise_id=promise.id,
                    old_status=old_status,
                    new_status=new_status,
                    note=update.get("note"),
                    source_url=article.get("url"),
                )
                db.add(status_update)
                total_updated += 1
                logger.info(
                    "Promise %d: %s → %s (confidence=%.2f)",
                    promise.id, old_status.value, new_status.value, confidence,
                )

    db.commit()
    return total_updated


def check_expired_promises(db: Session) -> int:
    """
    Bulk-expire all PENDING promises whose deadline has passed.
    No LLM call — purely time-based. Returns count updated.
    """
    now = datetime.now(timezone.utc)
    expired = (
        db.query(Promise)
        .filter(
            Promise.status == PromiseStatus.PENDING,
            Promise.deadline.isnot(None),
            Promise.deadline < now,
        )
        .all()
    )

    for promise in expired:
        old_status = promise.status
        promise.status = PromiseStatus.EXPIRED
        promise.resolved_at = now
        db.add(PromiseStatusUpdate(
            promise_id=promise.id,
            old_status=old_status,
            new_status=PromiseStatus.EXPIRED,
            note="Deadline passed without confirmed delivery (auto-expired by scheduler)",
        ))

    if expired:
        db.commit()
        logger.info("Auto-expired %d promises", len(expired))

    return len(expired)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d %b %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value[:19], fmt)
        except ValueError:
            continue
    return None
