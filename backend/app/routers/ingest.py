from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.extraction.pipeline import run_ingestion_pipeline
from app.models import IngestionJob, IngestionMode, Leader
from app.schemas import IngestRequest, IngestResponse

router = APIRouter(prefix="/ingest", tags=["ingestion"])


@router.post("/", response_model=IngestResponse)
def trigger_ingestion(
    payload: IngestRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Trigger an incremental ingestion run for a single leader + source."""
    leader = db.query(Leader).filter(Leader.id == payload.leader_id).first()
    if not leader:
        raise HTTPException(status_code=404, detail="Leader not found")

    job = IngestionJob(
        source_type=payload.source_type,
        mode=IngestionMode.INCREMENTAL,
        leader_id=payload.leader_id,
        status="pending",
        metadata={"query": payload.query, "max_articles": payload.max_articles},
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
        message=f"Incremental job {job.id} queued for {leader.name} ({payload.source_type})",
    )


@router.post("/backfill", response_model=IngestResponse)
def trigger_backfill(
    payload: IngestRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    One-time historical backfill for a leader.
    Fetches all available history regardless of last_ingested_at.
    """
    leader = db.query(Leader).filter(Leader.id == payload.leader_id).first()
    if not leader:
        raise HTTPException(status_code=404, detail="Leader not found")

    if leader.backfill_completed:
        raise HTTPException(
            status_code=409,
            detail=f"Backfill already completed for {leader.name}. Use /ingest/ for incremental runs.",
        )

    job = IngestionJob(
        source_type=payload.source_type,
        mode=IngestionMode.BACKFILL,
        leader_id=payload.leader_id,
        status="pending",
        metadata={"query": payload.query, "max_articles": payload.max_articles},
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
        message=f"Backfill job {job.id} queued for {leader.name} ({payload.source_type}). This may take a while.",
    )


@router.post("/backfill/all", response_model=list[IngestResponse])
def trigger_backfill_all(
    source_type: str = "all",
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
):
    """Trigger backfill for all active leaders that haven't been backfilled yet."""
    leaders = db.query(Leader).filter(
        Leader.is_active == True,
        Leader.backfill_completed == False,
    ).all()

    if not leaders:
        raise HTTPException(status_code=404, detail="No leaders pending backfill")

    responses = []
    for leader in leaders:
        job = IngestionJob(
            source_type=source_type,
            mode=IngestionMode.BACKFILL,
            leader_id=leader.id,
            status="pending",
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        payload = IngestRequest(leader_id=leader.id, source_type=source_type, max_articles=200)
        background_tasks.add_task(run_ingestion_pipeline, job.id, payload)

        responses.append(IngestResponse(
            job_id=job.id,
            status="pending",
            promises_extracted=0,
            contradictions_found=0,
            message=f"Backfill queued for {leader.name}",
        ))

    return responses


@router.post("/all-leaders", response_model=list[IngestResponse])
def trigger_all_leaders(
    source_type: str = "all",
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
):
    """Trigger incremental ingestion for all active leaders."""
    leaders = db.query(Leader).filter(Leader.is_active == True).all()
    if not leaders:
        raise HTTPException(status_code=404, detail="No active leaders found")

    responses = []
    for leader in leaders:
        job = IngestionJob(
            source_type=source_type,
            mode=IngestionMode.INCREMENTAL,
            leader_id=leader.id,
            status="pending",
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        payload = IngestRequest(leader_id=leader.id, source_type=source_type, max_articles=30)
        background_tasks.add_task(run_ingestion_pipeline, job.id, payload)

        responses.append(IngestResponse(
            job_id=job.id,
            status="pending",
            promises_extracted=0,
            contradictions_found=0,
            message=f"Incremental job queued for {leader.name}",
        ))

    return responses


@router.post("/jobs/{job_id}/retry", response_model=IngestResponse)
def retry_job(
    job_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Re-queue a failed job."""
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
        max_articles=(job.metadata or {}).get("max_articles", 30),
    )
    background_tasks.add_task(run_ingestion_pipeline, job.id, payload)

    return IngestResponse(
        job_id=job.id,
        status="pending",
        promises_extracted=0,
        contradictions_found=0,
        message=f"Job {job.id} re-queued",
    )


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


def _job_dict(job: IngestionJob) -> dict:
    return {
        "id": job.id,
        "source_type": job.source_type,
        "mode": job.mode.value,
        "leader_id": job.leader_id,
        "status": job.status,
        "promises_extracted": job.promises_extracted,
        "contradictions_found": job.contradictions_found,
        "articles_scraped": job.articles_scraped,
        "articles_deduped": job.articles_deduped,
        "error_message": job.error_message,
        "metadata": job.metadata,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "created_at": job.created_at,
    }
