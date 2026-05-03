import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import Base, engine
from app.routers import ingest, leaders, promises

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


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
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
