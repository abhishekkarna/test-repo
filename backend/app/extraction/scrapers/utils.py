"""Shared scraper utilities: retry logic, name normalization, text cleaning."""

import logging
import re
import time
from collections.abc import Callable
from typing import TypeVar

import httpx

from app.config import settings

logger = logging.getLogger(__name__)
T = TypeVar("T")


def with_retry(fn: Callable[[], T], label: str = "") -> T | None:
    """Call fn up to scraper_max_retries times with exponential backoff."""
    delays = [2 ** i for i in range(settings.scraper_max_retries)]
    for attempt, delay in enumerate(delays, 1):
        try:
            return fn()
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            if attempt == len(delays):
                logger.error("%s failed after %d attempts: %s", label, attempt, e)
                return None
            logger.warning("%s attempt %d failed (%s), retrying in %ds", label, attempt, e, delay)
            time.sleep(delay)
    return None


def make_client() -> httpx.Client:
    return httpx.Client(
        timeout=settings.scraper_request_timeout,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
        },
        follow_redirects=True,
    )


# Honorifics and titles to strip before Sansad name lookup
_HONORIFICS = re.compile(
    r"^(shri|smt|dr\.?|adv\.?|prof\.?|col\.?|lt\.?|gen\.?|capt\.?|mr\.?|ms\.?|mrs\.?)\s+",
    re.IGNORECASE,
)


def normalize_name_for_sansad(name: str) -> str:
    """
    Sansad.in stores names as 'SHRI FIRSTNAME LASTNAME'.
    Strip honorifics and return uppercase bare name.
    """
    name = _HONORIFICS.sub("", name.strip())
    return name.upper()


def clean_text(text: str) -> str:
    """Collapse whitespace and remove null bytes from scraped text."""
    text = text.replace("\x00", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def chunk_text(text: str, chunk_size: int = 6000) -> list[str]:
    """
    Split long text into chunks at paragraph boundaries.
    Used to feed long parliamentary PDFs to the LLM in pieces.
    """
    if len(text) <= chunk_size:
        return [text]

    paragraphs = text.split("\n\n")
    chunks, current = [], []
    current_len = 0

    for para in paragraphs:
        if current_len + len(para) > chunk_size and current:
            chunks.append("\n\n".join(current))
            current, current_len = [], 0
        current.append(para)
        current_len += len(para)

    if current:
        chunks.append("\n\n".join(current))

    return chunks
