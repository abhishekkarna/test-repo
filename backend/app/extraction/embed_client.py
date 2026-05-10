"""
Embedding client using sentence-transformers (free, local, no server required).
Model: all-MiniLM-L6-v2 — 384-dim, ~22MB, downloads once on first use.

Cosine similarity is computed with numpy for speed when comparing many candidates.
"""

import logging
from functools import lru_cache

import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)

_MODEL_NAME = "all-MiniLM-L6-v2"


@lru_cache(maxsize=1)
def _model():
    """Lazy-load the embedding model once per process."""
    try:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading embedding model %s", _MODEL_NAME)
        return SentenceTransformer(_MODEL_NAME)
    except Exception as e:
        logger.error("Failed to load embedding model: %s", e)
        return None


def get_embedding(text: str) -> list[float] | None:
    """Return a 384-dim embedding vector for text, or None on failure."""
    model = _model()
    if model is None:
        return None
    try:
        vec = model.encode(text, normalize_embeddings=True)
        return vec.tolist()
    except Exception as e:
        logger.warning("Embedding failed: %s", e)
        return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors (numpy-accelerated)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    # vectors are already L2-normalised by encode(..., normalize_embeddings=True)
    return float(np.dot(va, vb))


def find_nearest_duplicate(
    embedding: list[float],
    candidates: list[tuple[int, list[float]]],
    threshold: float | None = None,
) -> int | None:
    """
    Given an embedding and (id, embedding) candidates, return the ID of the
    most similar candidate whose similarity >= threshold, or None.
    """
    if not candidates or not embedding:
        return None
    cutoff = threshold if threshold is not None else settings.embedding_similarity_threshold
    query = np.array(embedding, dtype=np.float32)
    ids = [c[0] for c in candidates]
    matrix = np.array([c[1] for c in candidates], dtype=np.float32)
    sims = matrix @ query  # dot product; vectors are unit-normalised
    best_idx = int(np.argmax(sims))
    return ids[best_idx] if sims[best_idx] >= cutoff else None
