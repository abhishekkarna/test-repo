from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Contradiction, Promise, PromiseStatus, PromiseStatusUpdate
from app.schemas import ContradictionOut, PromiseCreate, PromiseOut, PromiseUpdate

router = APIRouter(prefix="/promises", tags=["promises"])


@router.get("/", response_model=list[PromiseOut])
def list_promises(
    leader_id: int | None = None,
    status: str | None = None,
    topic: str | None = None,
    verified: bool | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    q = db.query(Promise).options(
        selectinload(Promise.status_updates),
        selectinload(Promise.contradictions_as_original),
        selectinload(Promise.contradictions_as_new),
    )
    if leader_id is not None:
        q = q.filter(Promise.leader_id == leader_id)
    if status:
        q = q.filter(Promise.status == status)
    if topic:
        q = q.filter(Promise.topic == topic)
    if verified is not None:
        q = q.filter(Promise.verified == verified)

    promises = q.offset(offset).limit(limit).all()
    return [
        {**p.__dict__, "contradictions": p.contradictions_as_original + p.contradictions_as_new}
        for p in promises
    ]


@router.post("/", response_model=PromiseOut, status_code=201)
def create_promise(payload: PromiseCreate, db: Session = Depends(get_db)):
    promise = Promise(**payload.model_dump())
    db.add(promise)
    db.commit()
    db.refresh(promise)
    return {**promise.__dict__, "contradictions": [], "status_updates": []}


@router.get("/{promise_id}", response_model=PromiseOut)
def get_promise(promise_id: int, db: Session = Depends(get_db)):
    p = (
        db.query(Promise)
        .options(
            selectinload(Promise.status_updates),
            selectinload(Promise.contradictions_as_original),
            selectinload(Promise.contradictions_as_new),
        )
        .filter(Promise.id == promise_id)
        .first()
    )
    if not p:
        raise HTTPException(status_code=404, detail="Promise not found")
    return {**p.__dict__, "contradictions": p.contradictions_as_original + p.contradictions_as_new}


@router.patch("/{promise_id}/status", response_model=PromiseOut)
def update_promise_status(promise_id: int, payload: PromiseUpdate, db: Session = Depends(get_db)):
    p = db.query(Promise).filter(Promise.id == promise_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Promise not found")

    old_status = p.status
    if payload.status:
        p.status = payload.status
        if payload.status in (PromiseStatus.FULFILLED, PromiseStatus.BROKEN, PromiseStatus.EXPIRED):
            p.resolved_at = payload.resolved_at or datetime.utcnow()

        update = PromiseStatusUpdate(
            promise_id=p.id,
            old_status=old_status,
            new_status=payload.status,
            note=payload.note,
            source_url=payload.source_url,
        )
        db.add(update)

    db.commit()
    db.refresh(p)
    return {**p.__dict__, "contradictions": [], "status_updates": p.status_updates}


@router.patch("/{promise_id}/verify", response_model=PromiseOut)
def verify_promise(promise_id: int, db: Session = Depends(get_db)):
    p = db.query(Promise).filter(Promise.id == promise_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Promise not found")
    p.verified = True
    db.commit()
    db.refresh(p)
    return {**p.__dict__, "contradictions": [], "status_updates": p.status_updates}


@router.get("/{promise_id}/contradictions", response_model=list[ContradictionOut])
def get_contradictions(promise_id: int, db: Session = Depends(get_db)):
    contradictions = db.query(Contradiction).filter(
        (Contradiction.original_promise_id == promise_id)
        | (Contradiction.contradicting_promise_id == promise_id)
    ).all()
    return contradictions
