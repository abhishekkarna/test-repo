"""
Uses Claude to extract structured promise objects from raw text (news articles,
parliamentary debates, speeches). Also detects contradictions against existing
promises.
"""

import json
import logging
from datetime import datetime

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

SYSTEM_PROMPT = """You are a political analyst specializing in tracking politician promises and commitments.
Your task is to extract specific, concrete promises from political text.

A "promise" is:
- A specific commitment to DO something or DELIVER something
- Made by a named political leader
- Verifiable (can be checked if fulfilled or not)

NOT a promise:
- Vague aspirational statements ("we will work hard")
- General policy positions without commitment
- Past achievements being described
- Criticisms of opponents

Be conservative — only extract statements that are clearly promises, not opinions or goals."""


EXTRACT_PROMPT_TEMPLATE = """Analyze the following text from {source_type} and extract any promises made by {leader_name}.

Text:
---
{text}
---

Source: {source_title}
Date: {source_date}

Return a JSON array of promises. Each promise object must have:
{{
  "text": "exact quote or close paraphrase of the promise",
  "summary": "one sentence summary (max 100 chars)",
  "topic": one of ["economy", "healthcare", "education", "infrastructure", "agriculture", "defense", "environment", "social_welfare", "governance", "foreign_policy", "other"],
  "promised_at": "ISO date string or null",
  "deadline": "ISO date string if a timeframe is mentioned, else null",
  "confidence_score": float between 0 and 1 (how confident this is a real promise),
  "extraction_notes": "brief explanation of why this qualifies as a promise"
}}

If no promises are found, return an empty array [].
Return ONLY valid JSON, no other text."""


CONTRADICTION_PROMPT_TEMPLATE = """You are checking if a new political promise contradicts any existing promises by the same leader.

Leader: {leader_name}

New promise:
"{new_promise}"
(Made on: {new_date}, Topic: {topic})

Existing promises on the same topic:
{existing_promises}

For each existing promise that contradicts the new one, return a JSON array:
{{
  "existing_promise_id": <id>,
  "explanation": "clear explanation of the contradiction",
  "severity": one of ["minor", "moderate", "major"],
  "confidence_score": float 0-1
}}

Contradiction criteria:
- Direct reversal: promised X, now promises not-X
- Incompatible commitments: promises two things that cannot both be true
- Timeline contradiction: promised within 2 years, now says 5 years

NOT a contradiction:
- Refinements or additions to earlier promises
- Changed context (different geographic scope, updated fiscal conditions)
- Evolution of position with acknowledged change

Return [] if no contradictions. Return ONLY valid JSON."""


def extract_promises(
    text: str,
    leader_name: str,
    source_type: str,
    source_title: str,
    source_date: str | None = None,
) -> list[dict]:
    prompt = EXTRACT_PROMPT_TEMPLATE.format(
        leader_name=leader_name,
        source_type=source_type,
        source_title=source_title,
        source_date=source_date or "unknown",
        text=text[:8000],  # stay within context limits
    )

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        promises = json.loads(raw)
        if not isinstance(promises, list):
            return []
        return promises

    except (json.JSONDecodeError, anthropic.APIError) as e:
        logger.error("Extraction failed: %s", e)
        return []


def detect_contradictions(
    new_promise_text: str,
    new_promise_date: str | None,
    topic: str,
    leader_name: str,
    existing_promises: list[dict],
) -> list[dict]:
    if not existing_promises:
        return []

    existing_formatted = "\n".join(
        f'[ID {p["id"]}] "{p["summary"]}" (made: {p.get("promised_at", "unknown")})'
        for p in existing_promises
    )

    prompt = CONTRADICTION_PROMPT_TEMPLATE.format(
        leader_name=leader_name,
        new_promise=new_promise_text,
        new_date=new_promise_date or "unknown",
        topic=topic,
        existing_promises=existing_formatted,
    )

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        contradictions = json.loads(raw)
        if not isinstance(contradictions, list):
            return []
        return contradictions

    except (json.JSONDecodeError, anthropic.APIError) as e:
        logger.error("Contradiction detection failed: %s", e)
        return []
