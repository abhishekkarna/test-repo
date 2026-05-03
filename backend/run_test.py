"""
Standalone test: scrape articles for 1 leader, print results.
Uses SQLite (no Postgres needed) and skips LLM calls.
Run from backend/: python run_test.py
"""

import os
import sys
import json
import logging

# Point at SQLite for this test
os.environ["DATABASE_URL"] = "sqlite:///./test_run.db"
os.environ["LLM_PROVIDER"] = "ollama"  # won't actually call it
os.environ["SCHEDULER_ENABLED"] = "false"

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("test_run")

# ── Setup DB ──────────────────────────────────────────────────────────────────
from app.database import Base, engine, SessionLocal
from app.models import Leader

# SQLite doesn't support some Postgres-specific types used in Alembic, so just
# create tables directly via SQLAlchemy metadata
Base.metadata.create_all(bind=engine)

db = SessionLocal()

# Seed leader if not exists
leader = db.query(Leader).filter_by(name="Narendra Modi").first()
if not leader:
    leader = Leader(
        name="Narendra Modi",
        party="BJP",
        position="Prime Minister of India",
        constituency="Varanasi",
        state="Gujarat",
        country="India",
    )
    db.add(leader)
    db.commit()
    db.refresh(leader)
    logger.info("Seeded leader: %s (id=%d)", leader.name, leader.id)
else:
    logger.info("Leader already exists: %s (id=%d)", leader.name, leader.id)

db.close()

# ── Run scrapers ──────────────────────────────────────────────────────────────
from app.extraction.scrapers.news import fetch_news_articles
from app.extraction.scrapers.parliament import PIBScraper, PRSIndiaScraper

LEADER_NAME = "Narendra Modi"

print("\n" + "="*70)
print(f"SCRAPING ARTICLES FOR: {LEADER_NAME}")
print("="*70)

# 1. Google News RSS (no API key needed)
print("\n[1] Google News RSS")
print("-"*50)
news_articles = fetch_news_articles(LEADER_NAME, max_articles=5)
print(f"  Found {len(news_articles)} articles")
for i, a in enumerate(news_articles, 1):
    print(f"  {i}. {a['title'][:80]}")
    print(f"     URL: {a['url'][:80]}")
    print(f"     Date: {a.get('published_at', 'N/A')}")
    print(f"     Text preview: {(a.get('text') or '')[:120].strip()!r}")
    print()

# 2. PIB (press releases)
print("\n[2] PIB Press Releases (pib.gov.in)")
print("-"*50)
try:
    pib = PIBScraper()
    pib_articles = pib.fetch(LEADER_NAME, max_items=5)
    print(f"  Found {len(pib_articles)} articles")
    for i, a in enumerate(pib_articles, 1):
        print(f"  {i}. {a['title'][:80]}")
        print(f"     URL: {a['url'][:80]}")
        print(f"     Date: {a.get('published_at', 'N/A')}")
        print(f"     Text preview: {(a.get('text') or '')[:120].strip()!r}")
        print()
except Exception as e:
    print(f"  PIB scraper error: {e}")

# 3. PRS India (parliament debates)
print("\n[3] PRS India (Lok Sabha debates)")
print("-"*50)
try:
    prs = PRSIndiaScraper()
    prs_articles = prs.fetch(LEADER_NAME, max_items=3, house="loksabha")
    print(f"  Found {len(prs_articles)} articles")
    for i, a in enumerate(prs_articles, 1):
        print(f"  {i}. {a['title'][:80]}")
        print(f"     URL: {a['url'][:80]}")
        print(f"     Date: {a.get('published_at', 'N/A')}")
        print(f"     Text preview: {(a.get('text') or '')[:120].strip()!r}")
        print()
except Exception as e:
    print(f"  PRS scraper error: {e}")

# ── Summary ───────────────────────────────────────────────────────────────────
total = len(news_articles)
print("="*70)
print(f"TOTAL ARTICLES FETCHED: {total}")
print("="*70)

print("\nNote: LLM extraction skipped (no Ollama/Anthropic in this env).")
print("To run full pipeline, start Ollama with 'ollama pull llama3.1'")
print("then hit POST /ingest/backfill with leader_id=1")
