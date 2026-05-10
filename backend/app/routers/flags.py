from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.extraction.flagging import run_full_flagging_scan
from app.models import Flag, FlagSeverity, FlagType, Promise
from app.schemas import FlagOut, FlagReviewRequest

router = APIRouter(prefix="/flags", tags=["flags"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/", response_model=list[FlagOut])
def list_flags(
    leader_id: int | None = None,
    flag_type: FlagType | None = None,
    severity: FlagSeverity | None = None,
    reviewed: bool | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """List flags, optionally filtered. Unreviewed flags first."""
    q = db.query(Flag)
    if leader_id is not None:
        q = q.filter(Flag.leader_id == leader_id)
    if flag_type is not None:
        q = q.filter(Flag.flag_type == flag_type)
    if severity is not None:
        q = q.filter(Flag.severity == severity)
    if reviewed is not None:
        q = q.filter(Flag.reviewed == reviewed)
    return (
        q.order_by(Flag.reviewed.asc(), Flag.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


@router.get("/{flag_id}", response_model=FlagOut)
def get_flag(flag_id: int, db: Session = Depends(get_db)):
    flag = db.get(Flag, flag_id)
    if not flag:
        raise HTTPException(status_code=404, detail="Flag not found")
    return flag


@router.patch("/{flag_id}/review", response_model=FlagOut)
def review_flag(flag_id: int, body: FlagReviewRequest, db: Session = Depends(get_db)):
    """Mark a flag as reviewed (or un-reviewed)."""
    flag = db.get(Flag, flag_id)
    if not flag:
        raise HTTPException(status_code=404, detail="Flag not found")
    flag.reviewed = body.reviewed
    flag.reviewed_at = datetime.utcnow() if body.reviewed else None
    db.commit()
    db.refresh(flag)
    return flag


@router.post("/scan/{leader_id}")
def scan_leader(
    leader_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Trigger a full flagging scan for all promises of a leader (background)."""
    background_tasks.add_task(_scan_bg, leader_id)
    return {"status": "queued", "leader_id": leader_id}


@router.delete("/{flag_id}", status_code=204)
def delete_flag(flag_id: int, db: Session = Depends(get_db)):
    flag = db.get(Flag, flag_id)
    if not flag:
        raise HTTPException(status_code=404, detail="Flag not found")
    db.delete(flag)
    db.commit()


def _scan_bg(leader_id: int) -> None:
    from app.database import SessionLocal as SL
    db = SL()
    try:
        count = run_full_flagging_scan(leader_id, db)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Flagging scan failed for leader %d", leader_id)
    finally:
        db.close()
