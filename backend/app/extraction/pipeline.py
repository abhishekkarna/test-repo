"""
Main ingestion pipeline — orchestrates scraping, deduplication, LLM extraction,
contradiction detection, and status updates for a single leader + source type.

Two modes:
  BACKFILL    — one-time historical load, no date filter, paginate everything
  INCREMENTAL — daily scan, only articles since leader.last_ingested_at
"""

import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.extraction.extractor import detect_contradictions, extract_promises
from app.extraction.scrapers.news import fetch_news_articles
from app.extraction.scrapers.parliament import PRSIndiaScraper, PIBScraper, SansadScraper
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


# ── Public entry point ────────────────────────────────────────────────────────

def run_ingestion_pipeline(job_id: int, payload: IngestRequest) -> None:
    """
    Called as a background task from the ingest router.
    Creates its own DB session (cannot use FastAPI Depends here).
    """
    db = SessionLocal()
    try:
        _run(db, job_id, payload)
    except Exception as e:
        logger.exception("Pipeline crashed for job %d", job_id)
        _fail_job(db, job_id, str(e))
    finally:
        db.close()


# ── Core orchestration ────────────────────────────────────────────────────────

def _run(db: Session, job_id: int, payload: IngestRequest) -> None:
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
        "[job=%d] %s | leader=%s | source=%s | since=%s | max=%d",
        job_id, job.mode.value, leader.name, payload.source_type, since_date, max_items,
    )

    # 1. Scrape
    articles = _scrape(payload.source_type, leader.name, since_date, max_items)
    job.articles_scraped = len(articles)
    db.commit()

    # 2. Deduplicate
    fresh_articles = _deduplicate(articles, leader.id, db)
    job.articles_deduped = len(articles) - len(fresh_articles)
    db.commit()

    logger.info("[job=%d] %d articles fetched, %d fresh after dedup", job_id, len(articles), len(fresh_articles))

    total_promises = 0
    total_contradictions = 0

    # 3. Extract promises from each fresh article
    for article in fresh_articles:
        scraped_record = _get_scraped_record(db, article["url"])
        promises_from_article = _process_article(db, article, leader, job_id)
        total_promises += len(promises_from_article)

        # Update the scraped_articles record with extraction count
        if scraped_record:
            scraped_record.promises_extracted = len(promises_from_article)
            db.commit()

    # 4. Contradiction detection for newly added promises
    # (run after all extractions so we have the full new batch to compare against)
    new_promise_ids = _get_new_promise_ids(db, leader.id, job.started_at)
    for promise_id in new_promise_ids:
        promise = db.get(Promise, promise_id)
        if promise:
            contradictions = _check_contradictions(db, promise, leader.name)
            total_contradictions += len(contradictions)

    # 5. Status update pass — check fresh articles against existing pending promises
    if fresh_articles:
        run_status_update_pipeline(leader.id, fresh_articles, db)

    # 6. Mark job done
    job.status = "done"
    job.promises_extracted = total_promises
    job.contradictions_found = total_contradictions
    job.finished_at = datetime.now(timezone.utc)

    # 7. Update leader's last_ingested_at for incremental runs
    if not is_backfill:
        leader.last_ingested_at = datetime.now(timezone.utc)
    else:
        leader.backfill_completed = True
        leader.last_ingested_at = datetime.now(timezone.utc)

    db.commit()
    logger.info(
        "[job=%d] Done. promises=%d contradictions=%d",
        job_id, total_promises, total_contradictions,
    )


# ── Scraping ──────────────────────────────────────────────────────────────────

def _scrape(source_type: str, leader_name: str, since_date: datetime | None, max_items: int) -> list[dict]:
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
            articles = []
            articles += fetch_news_articles(leader_name, max_items // 4)
            articles += PRSIndiaScraper().fetch(leader_name, since_date, max_items // 4)
            articles += SansadScraper().fetch(leader_name, since_date, max_items // 4)
            articles += PIBScraper().fetch(leader_name, since_date, max_items // 4)
            return articles
    except Exception as e:
        logger.error("Scraping failed for source=%s leader=%s: %s", source_type, leader_name, e)
    return []


# ── Deduplication ─────────────────────────────────────────────────────────────

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _deduplicate(articles: list[dict], leader_id: int, db: Session) -> list[dict]:
    fresh = []
    for article in articles:
        url = article.get("url") or ""
        text = article.get("text") or ""
        url_hash = _sha256(url)
        content_hash = _sha256(text) if text else None

        # Skip if URL already processed
        if db.query(ScrapedArticle).filter_by(url_hash=url_hash).first():
            continue

        # Skip if same content already processed for this leader (syndication)
        if content_hash and db.query(ScrapedArticle).filter_by(
            leader_id=leader_id, content_hash=content_hash
        ).first():
            continue

        db.add(ScrapedArticle(
            url_hash=url_hash,
            url=url,
            source_type=article.get("source_type", "news_article"),
            leader_id=leader_id,
            title=article.get("title"),
            content_hash=content_hash,
        ))
        fresh.append(article)

    db.commit()
    return fresh


def _get_scraped_record(db: Session, url: str) -> ScrapedArticle | None:
    return db.query(ScrapedArticle).filter_by(url_hash=_sha256(url)).first()


# ── Promise extraction ────────────────────────────────────────────────────────

def _process_article(db: Session, article: dict, leader: Leader, job_id: int) -> list[Promise]:
    text = article.get("text") or ""
    if not text.strip():
        return []

    source_type_str = article.get("source_type", "news_article")
    title = article.get("title") or ""
    pub_date = article.get("published_at")
    url = article.get("url") or ""

    # Chunk long texts (e.g. parliament PDFs) and extract from each chunk
    chunks = chunk_text(text, chunk_size=6000)
    raw_extractions = []
    for chunk in chunks:
        extracted = extract_promises(
            text=chunk,
            leader_name=leader.name,
            source_type=source_type_str,
            source_title=title,
            source_date=pub_date,
        )
        raw_extractions.extend(extracted)

    # Retry with shorter context if Llama returned nothing on a non-empty article
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

        # Promise-level dedup: same summary for same leader = same promise
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
        db.flush()  # get promise.id before commit
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

    existing_dicts = [
        {"id": p.id, "summary": p.summary, "promised_at": str(p.promised_at)}
        for p in existing
    ]

    results = detect_contradictions(
        new_promise_text=promise.text,
        new_promise_date=str(promise.promised_at) if promise.promised_at else None,
        topic=promise.topic.value,
        leader_name=leader_name,
        existing_promises=existing_dicts,
    )

    saved = []
    for item in results:
        orig_id = item.get("existing_promise_id")
        if not orig_id:
            continue

        # Avoid duplicate contradiction records (unique constraint on the pair)
        existing_contradiction = db.query(Contradiction).filter_by(
            original_promise_id=orig_id,
            contradicting_promise_id=promise.id,
        ).first()
        if existing_contradiction:
            continue

        contradiction = Contradiction(
            original_promise_id=orig_id,
            contradicting_promise_id=promise.id,
            explanation=item.get("explanation", ""),
            severity=item.get("severity", "moderate"),
            confidence_score=item.get("confidence_score"),
        )
        db.add(contradiction)

        # Mark the newer promise as contradicted
        promise.status = PromiseStatus.CONTRADICTED
        saved.append(contradiction)

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
