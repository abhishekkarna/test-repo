"""
Two-phase ingestion pipeline.

Phase 1 — SCRAPE  : fetch articles → dedup → store full text in ScrapedArticle
Phase 2 — EXTRACT : read stored unprocessed articles → LLM extraction →
                    contradiction detection → status updates

This separation means:
  - Re-scraping is never needed to retry extraction
  - Models/prompts can be swapped without re-fetching
  - Scrape failures don't lose already-stored articles
"""

import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.extraction.extractor import detect_contradictions, extract_promises
from app.extraction.scrapers.news import fetch_news_articles
from app.extraction.scrapers.parliament import PIBScraper, PRSIndiaScraper, SansadScraper
from app.extraction.scrapers.utils import chunk_text
from app.extraction.status_pipeline import run_status_update_pipeline
from app.models import (
    Contradiction,
    IngestionJob,
    IngestionMode,
    Leader,
    Promise,
    PromiseStatus,
    PromiseTopic,
    ScrapedArticle,
    SourceType,
)
from app.schemas import IngestRequest

logger = logging.getLogger(__name__)

_SOURCE_TYPE_MAP = {
    "news_article": SourceType.NEWS_ARTICLE,
    "lok_sabha": SourceType.LOK_SABHA,
    "rajya_sabha": SourceType.RAJYA_SABHA,
    "press_release": SourceType.PRESS_RELEASE,
    "speech": SourceType.SPEECH,
    "manifesto": SourceType.MANIFESTO,
}

_TOPIC_MAP = {t.value: t for t in PromiseTopic}


# ── Public entry points ───────────────────────────────────────────────────────

def run_scrape_pipeline(job_id: int, payload: IngestRequest) -> None:
    """Phase 1: fetch articles and store raw text. No LLM calls."""
    db = SessionLocal()
    try:
        _run_scrape(db, job_id, payload)
    except Exception as e:
        logger.exception("Scrape pipeline crashed for job %d", job_id)
        _fail_job(db, job_id, str(e))
    finally:
        db.close()


def run_extract_pipeline(job_id: int, leader_id: int) -> None:
    """Phase 2: LLM extraction on stored unprocessed articles for a leader."""
    db = SessionLocal()
    try:
        _run_extract(db, job_id, leader_id)
    except Exception as e:
        logger.exception("Extract pipeline crashed for job %d", job_id)
        _fail_job(db, job_id, str(e))
    finally:
        db.close()


def run_ingestion_pipeline(job_id: int, payload: IngestRequest) -> None:
    """Legacy full pipeline (scrape + extract in one pass). Used by scheduler."""
    db = SessionLocal()
    try:
        _run_scrape(db, job_id, payload)
        _run_extract(db, job_id, payload.leader_id)
    except Exception as e:
        logger.exception("Full pipeline crashed for job %d", job_id)
        _fail_job(db, job_id, str(e))
    finally:
        db.close()


# ── Phase 1: Scrape ───────────────────────────────────────────────────────────

def _run_scrape(db: Session, job_id: int, payload: IngestRequest) -> None:
    job = db.get(IngestionJob, job_id)
    leader = db.get(Leader, payload.leader_id)
    if not job or not leader:
        return

    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    db.commit()

    is_backfill = job.mode == IngestionMode.BACKFILL
    since_date = None if is_backfill else leader.last_ingested_at
    max_items = payload.max_articles

    logger.info(
        "[job=%d phase=scrape] leader=%s source=%s since=%s max=%d",
        job_id, leader.name, payload.source_type, since_date, max_items,
    )

    raw_articles = _fetch(payload.source_type, leader.name, since_date, max_items)
    fresh = _store_articles(raw_articles, leader.id, db)

    job.articles_scraped = len(raw_articles)
    job.articles_deduped = len(raw_articles) - len(fresh)

    if job.phase == "scrape":
        job.status = "done"
        job.finished_at = datetime.now(timezone.utc)
        leader.last_ingested_at = datetime.now(timezone.utc)

    db.commit()
    logger.info(
        "[job=%d phase=scrape] fetched=%d stored=%d (deduped=%d)",
        job_id, len(raw_articles), len(fresh), len(raw_articles) - len(fresh),
    )


# ── Phase 2: Extract ──────────────────────────────────────────────────────────

def _run_extract(db: Session, job_id: int, leader_id: int) -> None:
    job = db.get(IngestionJob, job_id)
    leader = db.get(Leader, leader_id)
    if not job or not leader:
        return

    if job.phase in ("extract", "full"):
        job.status = "running"
        if not job.started_at:
            job.started_at = datetime.now(timezone.utc)
        db.commit()

    unprocessed = (
        db.query(ScrapedArticle)
        .filter_by(leader_id=leader_id, processed=False)
        .order_by(ScrapedArticle.scraped_at)
        .all()
    )

    logger.info(
        "[job=%d phase=extract] leader=%s unprocessed_articles=%d",
        job_id, leader.name, len(unprocessed),
    )

    total_promises = 0
    total_contradictions = 0
    extract_started_at = datetime.now(timezone.utc)

    for article_record in unprocessed:
        article_dict = {
            "title": article_record.title or "",
            "text": article_record.text or "",
            "url": article_record.url,
            "published_at": article_record.published_at,
            "source_type": article_record.source_type,
        }
        new_promises = _process_article(db, article_dict, leader, job_id)
        total_promises += len(new_promises)
        article_record.promises_extracted = len(new_promises)
        article_record.processed = True
        db.commit()

    # Contradiction detection on all newly extracted promises
    new_promise_ids = _get_new_promise_ids(db, leader_id, extract_started_at)
    for promise_id in new_promise_ids:
        promise = db.get(Promise, promise_id)
        if promise:
            total_contradictions += len(_check_contradictions(db, promise, leader.name))

    # Status update pass
    fresh_article_dicts = [
        {"title": a.title or "", "text": a.text or "", "url": a.url,
         "published_at": a.published_at, "source_type": a.source_type}
        for a in unprocessed if a.text
    ]
    if fresh_article_dicts:
        run_status_update_pipeline(leader_id, fresh_article_dicts, db)

    job.status = "done"
    job.promises_extracted = (job.promises_extracted or 0) + total_promises
    job.contradictions_found = (job.contradictions_found or 0) + total_contradictions
    job.finished_at = datetime.now(timezone.utc)

    if job.mode == IngestionMode.BACKFILL:
        leader.backfill_completed = True
    leader.last_ingested_at = datetime.now(timezone.utc)

    db.commit()
    logger.info(
        "[job=%d phase=extract] done. promises=%d contradictions=%d",
        job_id, total_promises, total_contradictions,
    )


# ── Fetching ──────────────────────────────────────────────────────────────────

def _fetch(source_type: str, leader_name: str, since_date: datetime | None, max_items: int) -> list[dict]:
    try:
        if source_type == "news":
            return fetch_news_articles(leader_name, max_items)
        if source_type in ("lok_sabha", "rajya_sabha"):
            house = "loksabha" if source_type == "lok_sabha" else "rajyasabha"
            return PRSIndiaScraper().fetch(leader_name, since_date, max_items, house=house)
        if source_type == "sansad":
            return SansadScraper().fetch(leader_name, since_date, max_items)
        if source_type == "pib":
            return PIBScraper().fetch(leader_name, since_date, max_items)
        if source_type == "all":
            per = max(1, max_items // 4)
            articles = []
            articles += fetch_news_articles(leader_name, per)
            articles += PRSIndiaScraper().fetch(leader_name, since_date, per)
            articles += SansadScraper().fetch(leader_name, since_date, per)
            articles += PIBScraper().fetch(leader_name, since_date, per)
            return articles
    except Exception as e:
        logger.error("Scraping failed source=%s leader=%s: %s", source_type, leader_name, e)
    return []


# ── Storage (Phase 1 write) ───────────────────────────────────────────────────

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _store_articles(articles: list[dict], leader_id: int, db: Session) -> list[ScrapedArticle]:
    """Dedup and persist articles with full text. Returns newly stored records."""
    stored = []
    for article in articles:
        url = article.get("url") or ""
        text = article.get("text") or ""
        url_hash = _sha256(url)
        content_hash = _sha256(text) if text else None

        if db.query(ScrapedArticle).filter_by(url_hash=url_hash).first():
            continue
        if content_hash and db.query(ScrapedArticle).filter_by(
            leader_id=leader_id, content_hash=content_hash
        ).first():
            continue

        record = ScrapedArticle(
            url_hash=url_hash,
            url=url,
            source_type=article.get("source_type", "news_article"),
            leader_id=leader_id,
            title=article.get("title"),
            published_at=article.get("published_at"),
            text=text or None,
            content_hash=content_hash,
            processed=False,
        )
        db.add(record)
        stored.append(record)

    db.commit()
    return stored


# ── Promise extraction (Phase 2) ──────────────────────────────────────────────

def _process_article(db: Session, article: dict, leader: Leader, job_id: int) -> list[Promise]:
    text = article.get("text") or ""
    if not text.strip():
        return []

    source_type_str = article.get("source_type", "news_article")
    title = article.get("title") or ""
    pub_date = article.get("published_at")
    url = article.get("url") or ""

    chunks = chunk_text(text, chunk_size=6000)
    raw_extractions = []
    for chunk in chunks:
        raw_extractions.extend(extract_promises(
            text=chunk,
            leader_name=leader.name,
            source_type=source_type_str,
            source_title=title,
            source_date=pub_date,
        ))

    if not raw_extractions and len(text) > 500:
        raw_extractions = extract_promises(
            text=text[:3000],
            leader_name=leader.name,
            source_type=source_type_str,
            source_title=title,
            source_date=pub_date,
        )

    saved = []
    seen_hashes: set[str] = set()

    for item in raw_extractions:
        confidence = item.get("confidence_score") or 0.0
        if confidence < settings.min_confidence_score:
            continue

        summary = (item.get("summary") or "").strip()
        if not summary:
            continue

        promise_hash = _sha256(f"{leader.id}:{summary.lower()}")
        if promise_hash in seen_hashes:
            continue
        seen_hashes.add(promise_hash)

        if db.query(Promise).filter_by(leader_id=leader.id, content_hash=promise_hash).first():
            continue

        topic = _TOPIC_MAP.get(item.get("topic", "other"), PromiseTopic.OTHER)
        source_type_enum = _SOURCE_TYPE_MAP.get(source_type_str, SourceType.NEWS_ARTICLE)

        promise = Promise(
            leader_id=leader.id,
            text=item.get("text") or summary,
            summary=summary,
            topic=topic,
            status=PromiseStatus.PENDING,
            promised_at=_parse_date(item.get("promised_at") or pub_date),
            deadline=_parse_date(item.get("deadline")),
            source_url=url,
            source_type=source_type_enum,
            source_title=title,
            confidence_score=confidence,
            extraction_notes=item.get("extraction_notes"),
            raw_context=text[:500],
            content_hash=promise_hash,
        )
        db.add(promise)
        db.flush()
        saved.append(promise)

    db.commit()
    return saved


def _get_new_promise_ids(db: Session, leader_id: int, since: datetime) -> list[int]:
    rows = (
        db.query(Promise.id)
        .filter(Promise.leader_id == leader_id, Promise.created_at >= since)
        .all()
    )
    return [r[0] for r in rows]


# ── Contradiction detection ───────────────────────────────────────────────────

def _check_contradictions(db: Session, promise: Promise, leader_name: str) -> list[Contradiction]:
    existing = (
        db.query(Promise)
        .filter(
            Promise.leader_id == promise.leader_id,
            Promise.topic == promise.topic,
            Promise.id != promise.id,
        )
        .order_by(Promise.promised_at.desc())
        .limit(settings.contradiction_batch_size)
        .all()
    )
    if not existing:
        return []

    results = detect_contradictions(
        new_promise_text=promise.text,
        new_promise_date=str(promise.promised_at) if promise.promised_at else None,
        topic=promise.topic.value,
        leader_name=leader_name,
        existing_promises=[
            {"id": p.id, "summary": p.summary, "promised_at": str(p.promised_at)}
            for p in existing
        ],
    )

    saved = []
    for item in results:
        orig_id = item.get("existing_promise_id")
        if not orig_id:
            continue
        if db.query(Contradiction).filter_by(
            original_promise_id=orig_id,
            contradicting_promise_id=promise.id,
        ).first():
            continue

        db.add(Contradiction(
            original_promise_id=orig_id,
            contradicting_promise_id=promise.id,
            explanation=item.get("explanation", ""),
            severity=item.get("severity", "moderate"),
            confidence_score=item.get("confidence_score"),
        ))
        promise.status = PromiseStatus.CONTRADICTED
        saved.append(True)

    db.commit()
    return saved


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d %b %Y", "%d/%m/%Y", "%B %d, %Y"):
        try:
            return datetime.strptime(value[:19], fmt)
        except ValueError:
            continue
    return None


def _fail_job(db: Session, job_id: int, error: str) -> None:
    try:
        job = db.get(IngestionJob, job_id)
        if job:
            job.status = "failed"
            job.error_message = error[:1000]
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
    except Exception:
        pass
