import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import Base, engine
from app.routers import flags, ingest, leaders, promises

_STATIC = Path(__file__).parent / "static"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="Leader Promise Tracker",
    description="Track promises made by political leaders, their status, and contradictions.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(leaders.router)
app.include_router(promises.router)
app.include_router(ingest.router)
app.include_router(flags.router)

app.mount("/ui", StaticFiles(directory=_STATIC, html=True), name="ui")


@app.get("/", include_in_schema=False)
def root():
    return FileResponse(_STATIC / "index.html")


def _seed_initial_data() -> None:
    from app.database import SessionLocal
    from app.models import Leader
    db = SessionLocal()
    try:
        if not db.query(Leader).first():
            db.add(Leader(
                name="Narendra Modi",
                party="Bharatiya Janata Party",
                position="Prime Minister of India",
                constituency="Varanasi",
                state="Gujarat",
                country="India",
                is_active=True,
            ))
            db.commit()
            logging.getLogger(__name__).info("Seeded initial leader: Narendra Modi")
    finally:
        db.close()


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    _seed_initial_data()
    if settings.scheduler_enabled:
        from app.scheduler import start_scheduler
        start_scheduler()


@app.on_event("shutdown")
def shutdown():
    if settings.scheduler_enabled:
        from app.scheduler import stop_scheduler
        stop_scheduler()


@app.get("/health")
def health():
    return {"status": "ok", "llm_provider": settings.llm_provider}
