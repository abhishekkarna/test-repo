from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Leader, Promise
from app.schemas import LeaderCreate, LeaderOut, PromiseOut

router = APIRouter(prefix="/leaders", tags=["leaders"])


@router.get("/", response_model=list[LeaderOut])
def list_leaders(
    state: str | None = None,
    is_active: bool = True,
    limit: int = 20,
    db: Session = Depends(get_db),
):
    q = db.query(Leader).filter(Leader.is_active == is_active)
    if state:
        q = q.filter(Leader.state == state)
    leaders = q.limit(limit).all()
    # Attach promise stats without lazy-loading issues
    for leader in leaders:
        _ = leader.promises
    return leaders


@router.post("/", response_model=LeaderOut, status_code=201)
def create_leader(payload: LeaderCreate, db: Session = Depends(get_db)):
    leader = Leader(**payload.model_dump())
    db.add(leader)
    db.commit()
    db.refresh(leader)
    return leader


@router.get("/{leader_id}", response_model=LeaderOut)
def get_leader(leader_id: int, db: Session = Depends(get_db)):
    leader = (
        db.query(Leader)
        .options(selectinload(Leader.promises))
        .filter(Leader.id == leader_id)
        .first()
    )
    if not leader:
        raise HTTPException(status_code=404, detail="Leader not found")
    return leader


@router.get("/{leader_id}/promises", response_model=list[PromiseOut])
def get_leader_promises(
    leader_id: int,
    topic: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    leader = db.query(Leader).filter(Leader.id == leader_id).first()
    if not leader:
        raise HTTPException(status_code=404, detail="Leader not found")

    q = (
        db.query(Promise)
        .options(
            selectinload(Promise.status_updates),
            selectinload(Promise.contradictions_as_original),
            selectinload(Promise.contradictions_as_new),
        )
        .filter(Promise.leader_id == leader_id)
    )
    if topic:
        q = q.filter(Promise.topic == topic)
    if status:
        q = q.filter(Promise.status == status)

    promises = q.order_by(desc(Promise.promised_at)).all()

    # Merge both contradiction directions for each promise
    result = []
    for p in promises:
        p_dict = {
            **p.__dict__,
            "contradictions": p.contradictions_as_original + p.contradictions_as_new,
            "status_updates": p.status_updates,
        }
        result.append(p_dict)
    return result


@router.delete("/{leader_id}", status_code=204)
def delete_leader(leader_id: int, db: Session = Depends(get_db)):
    leader = db.query(Leader).filter(Leader.id == leader_id).first()
    if not leader:
        raise HTTPException(status_code=404, detail="Leader not found")
    db.delete(leader)
    db.commit()
