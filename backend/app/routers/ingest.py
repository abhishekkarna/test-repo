from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.extraction.pipeline import run_extract_pipeline, run_ingestion_pipeline, run_scrape_pipeline, run_windowed_backfill_pipeline
from app.models import IngestionJob, IngestionMode, Leader, ScrapedArticle
from app.schemas import IngestRequest, IngestResponse, WindowedBackfillRequest

router = APIRouter(prefix="/ingest", tags=["ingestion"])


# ── Phase 1: Scrape-only ──────────────────────────────────────────────────────

@router.post("/scrape", response_model=IngestResponse)
def trigger_scrape(
    payload: IngestRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Phase 1 — fetch and store raw articles. No LLM calls."""
    leader = db.query(Leader).filter(Leader.id == payload.leader_id).first()
    if not leader:
        raise HTTPException(status_code=404, detail="Leader not found")

    job = IngestionJob(
        source_type=payload.source_type,
        phase="scrape",
        mode=IngestionMode.INCREMENTAL,
        leader_id=payload.leader_id,
        status="pending",
        job_metadata={"max_articles": payload.max_articles},
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    background_tasks.add_task(run_scrape_pipeline, job.id, payload)

    return IngestResponse(
        job_id=job.id,
        status="pending",
        promises_extracted=0,
        contradictions_found=0,
        message=f"Scrape job {job.id} queued for {leader.name} ({payload.source_type})",
    )


# ── Phase 2: Extract-only ─────────────────────────────────────────────────────

@router.post("/extract/{leader_id}", response_model=IngestResponse)
def trigger_extract(
    leader_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Phase 2 — run LLM extraction on all stored unprocessed articles for a leader."""
    leader = db.query(Leader).filter(Leader.id == leader_id).first()
    if not leader:
        raise HTTPException(status_code=404, detail="Leader not found")

    pending_count = db.query(ScrapedArticle).filter_by(leader_id=leader_id, processed=False).count()
    if pending_count == 0:
        raise HTTPException(status_code=404, detail="No unprocessed articles found. Run /ingest/scrape first.")

    job = IngestionJob(
        source_type="stored",
        phase="extract",
        mode=IngestionMode.INCREMENTAL,
        leader_id=leader_id,
        status="pending",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    background_tasks.add_task(run_extract_pipeline, job.id, leader_id)

    return IngestResponse(
        job_id=job.id,
        status="pending",
        promises_extracted=0,
        contradictions_found=0,
        message=f"Extract job {job.id} queued for {leader.name} ({pending_count} articles to process)",
    )


# ── Full pipeline (scrape + extract) ─────────────────────────────────────────

@router.post("/", response_model=IngestResponse)
def trigger_ingestion(
    payload: IngestRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Full pipeline — scrape then extract in one job."""
    leader = db.query(Leader).filter(Leader.id == payload.leader_id).first()
    if not leader:
        raise HTTPException(status_code=404, detail="Leader not found")

    job = IngestionJob(
        source_type=payload.source_type,
        phase="full",
        mode=IngestionMode.INCREMENTAL,
        leader_id=payload.leader_id,
        status="pending",
        job_metadata={"max_articles": payload.max_articles},
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    background_tasks.add_task(run_ingestion_pipeline, job.id, payload)

    return IngestResponse(
        job_id=job.id,
        status="pending",
        promises_extracted=0,
        contradictions_found=0,
        message=f"Full pipeline job {job.id} queued for {leader.name} ({payload.source_type})",
    )


@router.post("/backfill/windowed", response_model=IngestResponse)
def trigger_windowed_backfill(
    payload: WindowedBackfillRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Year-windowed historical backfill. Scrapes one year at a time from start_year
    to end_year, then runs LLM extraction once on all stored articles.
    Poll GET /ingest/jobs/{job_id} to see per-year progress in job.metadata.
    """
    leader = db.query(Leader).filter(Leader.id == payload.leader_id).first()
    if not leader:
        raise HTTPException(status_code=404, detail="Leader not found")

    n_years = payload.end_year - payload.start_year + 1
    job = IngestionJob(
        source_type=payload.source_type,
        phase="full",
        mode=IngestionMode.BACKFILL,
        leader_id=payload.leader_id,
        status="pending",
        job_metadata={
            "start_year": payload.start_year,
            "end_year": payload.end_year,
            "articles_per_window": payload.articles_per_window,
            "total_years": n_years,
        },
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    background_tasks.add_task(
        run_windowed_backfill_pipeline,
        job.id,
        payload.leader_id,
        payload.start_year,
        payload.end_year,
        payload.source_type,
        payload.articles_per_window,
    )

    return IngestResponse(
        job_id=job.id,
        status="pending",
        promises_extracted=0,
        contradictions_found=0,
        message=(
            f"Windowed backfill queued for {leader.name} "
            f"({payload.start_year}–{payload.end_year}, {n_years} year-windows, "
            f"{payload.articles_per_window} articles/window)"
        ),
    )


@router.post("/backfill", response_model=IngestResponse)
def trigger_backfill(
    payload: IngestRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """One-time historical backfill — scrape then extract."""
    leader = db.query(Leader).filter(Leader.id == payload.leader_id).first()
    if not leader:
        raise HTTPException(status_code=404, detail="Leader not found")

    if leader.backfill_completed:
        raise HTTPException(
            status_code=409,
            detail=f"Backfill already completed for {leader.name}. Use /ingest/scrape for incremental.",
        )

    job = IngestionJob(
        source_type=payload.source_type,
        phase="full",
        mode=IngestionMode.BACKFILL,
        leader_id=payload.leader_id,
        status="pending",
        job_metadata={"max_articles": payload.max_articles},
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    background_tasks.add_task(run_ingestion_pipeline, job.id, payload)

    return IngestResponse(
        job_id=job.id,
        status="pending",
        promises_extracted=0,
        contradictions_found=0,
        message=f"Backfill job {job.id} queued for {leader.name}. This may take a while.",
    )


@router.post("/backfill/all", response_model=list[IngestResponse])
def trigger_backfill_all(
    source_type: str = "all",
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
):
    """Trigger backfill for all active leaders not yet backfilled."""
    leaders = db.query(Leader).filter(Leader.is_active == True, Leader.backfill_completed == False).all()
    if not leaders:
        raise HTTPException(status_code=404, detail="No leaders pending backfill")

    responses = []
    for leader in leaders:
        job = IngestionJob(source_type=source_type, phase="full", mode=IngestionMode.BACKFILL,
                           leader_id=leader.id, status="pending")
        db.add(job)
        db.commit()
        db.refresh(job)
        payload = IngestRequest(leader_id=leader.id, source_type=source_type, max_articles=200)
        background_tasks.add_task(run_ingestion_pipeline, job.id, payload)
        responses.append(IngestResponse(job_id=job.id, status="pending", promises_extracted=0,
                                        contradictions_found=0, message=f"Backfill queued for {leader.name}"))
    return responses


@router.post("/all-leaders", response_model=list[IngestResponse])
def trigger_all_leaders(
    source_type: str = "all",
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
):
    """Trigger incremental full pipeline for all active leaders."""
    leaders = db.query(Leader).filter(Leader.is_active == True).all()
    if not leaders:
        raise HTTPException(status_code=404, detail="No active leaders found")

    responses = []
    for leader in leaders:
        job = IngestionJob(source_type=source_type, phase="full", mode=IngestionMode.INCREMENTAL,
                           leader_id=leader.id, status="pending")
        db.add(job)
        db.commit()
        db.refresh(job)
        payload = IngestRequest(leader_id=leader.id, source_type=source_type, max_articles=30)
        background_tasks.add_task(run_ingestion_pipeline, job.id, payload)
        responses.append(IngestResponse(job_id=job.id, status="pending", promises_extracted=0,
                                        contradictions_found=0, message=f"Full pipeline queued for {leader.name}"))
    return responses


# ── Raw article browser ───────────────────────────────────────────────────────

@router.get("/articles", response_model=list[dict])
def list_articles(
    leader_id: int | None = None,
    processed: bool | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """Browse stored raw articles. Filter by leader or processed status."""
    q = db.query(ScrapedArticle)
    if leader_id is not None:
        q = q.filter(ScrapedArticle.leader_id == leader_id)
    if processed is not None:
        q = q.filter(ScrapedArticle.processed == processed)
    articles = q.order_by(ScrapedArticle.scraped_at.desc()).offset(offset).limit(limit).all()
    return [_article_dict(a) for a in articles]


@router.get("/articles/{article_id}", response_model=dict)
def get_article(article_id: int, db: Session = Depends(get_db)):
    """Get a single stored article including full text."""
    article = db.query(ScrapedArticle).filter(ScrapedArticle.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    return _article_dict(article, include_text=True)


# ── Job management ────────────────────────────────────────────────────────────

@router.post("/jobs/{job_id}/retry", response_model=IngestResponse)
def retry_job(job_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    job = db.query(IngestionJob).filter(IngestionJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "failed":
        raise HTTPException(status_code=409, detail=f"Job is '{job.status}', only failed jobs can be retried")

    job.status = "pending"
    job.error_message = None
    job.started_at = None
    job.finished_at = None
    db.commit()

    payload = IngestRequest(
        leader_id=job.leader_id,
        source_type=job.source_type,
        max_articles=(job.job_metadata or {}).get("max_articles", 30),
    )
    background_tasks.add_task(run_ingestion_pipeline, job.id, payload)

    return IngestResponse(job_id=job.id, status="pending", promises_extracted=0,
                          contradictions_found=0, message=f"Job {job.id} re-queued")


@router.get("/jobs", response_model=list[dict])
def list_jobs(limit: int = 20, db: Session = Depends(get_db)):
    jobs = db.query(IngestionJob).order_by(IngestionJob.created_at.desc()).limit(limit).all()
    return [_job_dict(j) for j in jobs]


@router.get("/jobs/{job_id}", response_model=dict)
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(IngestionJob).filter(IngestionJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_dict(job)


# ── Serializers ───────────────────────────────────────────────────────────────

def _job_dict(job: IngestionJob) -> dict:
    return {
        "id": job.id,
        "source_type": job.source_type,
        "phase": job.phase,
        "mode": job.mode.value,
        "leader_id": job.leader_id,
        "status": job.status,
        "promises_extracted": job.promises_extracted,
        "contradictions_found": job.contradictions_found,
        "articles_scraped": job.articles_scraped,
        "articles_deduped": job.articles_deduped,
        "error_message": job.error_message,
        "metadata": job.job_metadata,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "created_at": job.created_at,
    }


def _article_dict(a: ScrapedArticle, include_text: bool = False) -> dict:
    d = {
        "id": a.id,
        "leader_id": a.leader_id,
        "title": a.title,
        "url": a.url,
        "source_type": a.source_type,
        "published_at": a.published_at,
        "processed": a.processed,
        "promises_extracted": a.promises_extracted,
        "scraped_at": a.scraped_at,
        "text_length": len(a.text) if a.text else 0,
    }
    if include_text:
        d["text"] = a.text
    return d
