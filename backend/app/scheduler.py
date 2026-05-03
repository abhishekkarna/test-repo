"""
APScheduler setup.

Nightly job (2 AM IST by default):
  - Runs incremental ingestion for all active leaders across all source types
  - Auto-expires promises whose deadline has passed
"""

import logging
import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler(timezone=settings.scheduler_timezone)

_SOURCE_TYPES = ["news", "lok_sabha", "rajya_sabha", "pib"]


def nightly_ingestion_job() -> None:
    # Import here to avoid circular imports at module load time
    from app.database import SessionLocal
    from app.extraction.pipeline import run_ingestion_pipeline
    from app.extraction.status_pipeline import check_expired_promises
    from app.models import IngestionJob, IngestionMode, Leader
    from app.schemas import IngestRequest

    db = SessionLocal()
    try:
        leaders = db.query(Leader).filter(Leader.is_active == True).all()
        logger.info("Scheduler: nightly run for %d leaders", len(leaders))

        for leader in leaders:
            for source_type in _SOURCE_TYPES:
                job = IngestionJob(
                    source_type=source_type,
                    mode=IngestionMode.INCREMENTAL,
                    leader_id=leader.id,
                    status="pending",
                )
                db.add(job)
                db.commit()
                db.refresh(job)

                payload = IngestRequest(
                    leader_id=leader.id,
                    source_type=source_type,
                    max_articles=30,
                )
                try:
                    run_ingestion_pipeline(job.id, payload)
                except Exception as e:
                    logger.error(
                        "Scheduler: job failed leader=%s source=%s: %s",
                        leader.name, source_type, e,
                    )

                time.sleep(3)  # stagger to avoid hammering LLM / scrapers

        check_expired_promises(db)
        logger.info("Scheduler: nightly run complete")

    finally:
        db.close()


def start_scheduler() -> None:
    scheduler.add_job(
        nightly_ingestion_job,
        CronTrigger(hour=settings.scheduler_cron_hour, timezone=settings.scheduler_timezone),
        id="nightly_ingestion",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started — nightly job at %02d:00 %s",
        settings.scheduler_cron_hour,
        settings.scheduler_timezone,
    )


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
