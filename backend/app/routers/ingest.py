from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.extraction.pipeline import run_ingestion_pipeline
from app.models import IngestionJob, Leader
from app.schemas import IngestRequest, IngestResponse

router = APIRouter(prefix="/ingest", tags=["ingestion"])


@router.post("/", response_model=IngestResponse)
def trigger_ingestion(
    payload: IngestRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    leader = db.query(Leader).filter(Leader.id == payload.leader_id).first()
    if not leader:
        raise HTTPException(status_code=404, detail="Leader not found")

    job = IngestionJob(
        source_type=payload.source_type,
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
        message=f"Ingestion job {job.id} queued for {leader.name}",
    )


@router.get("/jobs", response_model=list[dict])
def list_jobs(limit: int = 20, db: Session = Depends(get_db)):
    jobs = db.query(IngestionJob).order_by(IngestionJob.created_at.desc()).limit(limit).all()
    return [
        {
            "id": j.id,
            "source_type": j.source_type,
            "leader_id": j.leader_id,
            "status": j.status,
            "promises_extracted": j.promises_extracted,
            "contradictions_found": j.contradictions_found,
            "error_message": j.error_message,
            "started_at": j.started_at,
            "finished_at": j.finished_at,
            "created_at": j.created_at,
        }
        for j in jobs
    ]


@router.get("/jobs/{job_id}", response_model=dict)
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(IngestionJob).filter(IngestionJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "id": job.id,
        "source_type": job.source_type,
        "leader_id": job.leader_id,
        "status": job.status,
        "promises_extracted": job.promises_extracted,
        "contradictions_found": job.contradictions_found,
        "error_message": job.error_message,
        "metadata": job.metadata,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "created_at": job.created_at,
    }
