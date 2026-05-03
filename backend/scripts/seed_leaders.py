"""
Seed the top 3 Indian political leaders.
Run from backend/: python -m scripts.seed_leaders

Idempotent — safe to re-run, skips leaders that already exist by name.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import SessionLocal
from app.models import Leader

LEADERS = [
    {
        "name": "Narendra Modi",
        "party": "BJP",
        "position": "Prime Minister of India",
        "constituency": "Varanasi",
        "state": "Gujarat",
        "country": "India",
    },
    {
        "name": "Rahul Gandhi",
        "party": "INC",
        "position": "Leader of Opposition",
        "constituency": "Rae Bareli",
        "state": "Kerala",
        "country": "India",
    },
    {
        "name": "Amit Shah",
        "party": "BJP",
        "position": "Home Minister of India",
        "constituency": "Gandhinagar",
        "state": "Gujarat",
        "country": "India",
    },
]


def seed():
    db = SessionLocal()
    try:
        existing_names = {r[0] for r in db.query(Leader.name).all()}
        added = 0
        for data in LEADERS:
            if data["name"] in existing_names:
                print(f"  skip (exists): {data['name']}")
                continue
            db.add(Leader(**data))
            added += 1
            print(f"  added: {data['name']} ({data['party']} — {data['position']})")
        db.commit()
        print(f"\nDone. Added {added} leader(s), {len(LEADERS) - added} already existed.")
    finally:
        db.close()


if __name__ == "__main__":
    print("Seeding leaders...")
    seed()
