"""
Promise linker: after a batch of new promises is extracted, compute embeddings
and link each new promise to an existing near-duplicate (same leader, same idea
phrased differently across articles) by setting near_duplicate_of.

A near-duplicate is NOT a contradiction — it's the same commitment extracted
from multiple sources. We keep both records for provenance but link them so
downstream views can collapse duplicates.

Threshold: settings.embedding_similarity_threshold (default 0.85).
"""

import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.extraction.embed_client import find_nearest_duplicate, get_embedding
from app.models import Promise

logger = logging.getLogger(__name__)


def embed_and_link(promise_ids: list[int], db: Session) -> int:
    """
    For each promise ID in the list:
      1. Generate and store its embedding (if missing).
      2. Compare against existing promises for the same leader.
      3. Set near_duplicate_of if a match above threshold is found.

    Returns the number of near-duplicate links created.
    """
    if not promise_ids:
        return 0

    promises = db.query(Promise).filter(Promise.id.in_(promise_ids)).all()
    if not promises:
        return 0

    leader_id = promises[0].leader_id
    links_created = 0

    # Load all existing promises for this leader that already have embeddings,
    # excluding the current batch — these are candidates for matching.
    batch_id_set = set(promise_ids)
    candidates_raw = (
        db.query(Promise.id, Promise.embedding)
        .filter(
            Promise.leader_id == leader_id,
            Promise.id.notin_(batch_id_set),
            Promise.embedding.isnot(None),
            Promise.near_duplicate_of.is_(None),  # only canonical promises as targets
        )
        .limit(settings.embedding_candidate_limit)
        .all()
    )
    candidates: list[tuple[int, list[float]]] = [
        (row.id, row.embedding) for row in candidates_raw if row.embedding
    ]

    for promise in promises:
        # Step 1: generate embedding if missing
        if promise.embedding is None:
            vec = get_embedding(promise.summary)
            if vec is None:
                logger.warning("Embedding failed for promise %d, skipping link", promise.id)
                continue
            promise.embedding = vec
        else:
            vec = promise.embedding

        # Step 2: find nearest existing promise
        if candidates:
            match_id = find_nearest_duplicate(vec, candidates)
            if match_id is not None and match_id != promise.id:
                promise.near_duplicate_of = match_id
                links_created += 1
                logger.info(
                    "Promise %d linked as near-duplicate of %d", promise.id, match_id
                )

        # Add this promise to the candidate pool for later promises in this batch
        # (so within-batch duplicates are also caught)
        candidates.append((promise.id, vec))

    db.commit()
    return links_created
