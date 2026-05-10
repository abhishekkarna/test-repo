"""
Extracts structured promise objects from raw text and detects contradictions.
Uses the configured LLM provider (Ollama/Llama by default, Anthropic for production).
"""

import json
import logging

from app.extraction.llm_client import llm

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a fact-checker building a public accountability database of politician promises.
Your job is to extract only SPECIFIC, TRACKABLE promises — statements where a neutral observer
could later determine with confidence whether the promise was kept or broken.

PROMISE SIGNAL PHRASES — a statement counts as a promise if it uses any of these forms:
  • Direct:     "I will", "We will", "I shall", "I promise", "I pledge", "I guarantee"
  • Committed:  "We are committed to", "The government is committed to", "I am committed to"
  • Goal/Target:"Our target is", "Our goal is", "We aim to", "We target", "Our objective is"
  • Ensure:     "We will ensure", "The government will ensure", "I will ensure"
  • Intent:     "We intend to", "We plan to", "The government plans to"
  • Priority:   "Our priority is [specific deliverable]", "The top priority will be"
  • Determined: "We are determined to", "I am determined to achieve"
  All of the above count as promises ONLY if a concrete anchor (see below) is also present.

A promise QUALIFIES only if it has at least one concrete anchor:
  • A specific number or amount  ("₹10,000 crore", "2 crore homes", "50% increase")
  • A named scheme, bill, or program  ("PM Kisan", "Ayushman Bharat", "CAA implementation")
  • A concrete action with an observable outcome  ("inaugurate X", "pass Y bill", "build Z highway")
  • A specific deadline or timeframe  ("by 2025", "within 100 days", "before the next budget")
  • A named, specific beneficiary group  ("farmers in Vidarbha", "MSMEs under ₹5 crore turnover")

REJECT anything that:
  • Is vague or aspirational  ("improve governance", "bring development", "fight corruption")
  • Cannot be verified true/false  ("work for the people", "ensure prosperity")
  • Is a general policy stance, not a commitment  ("we believe in federalism")
  • Describes past achievements  ("we have already built 10 lakh homes")
  • Is a criticism of opponents  ("they failed to deliver X")
  • Is a conditional wish with no commitment  ("if elected, we hope to...")

Confidence scoring guide:
  0.9–1.0  Explicit first-person commitment with number + deadline  ("I will build 5 AIIMS by 2026")
  0.7–0.9  Clear commitment with either a number OR a deadline, not both
  0.5–0.7  Commitment with a named scheme but no specific number or deadline ("we are committed to PM Kisan")
  below 0.5  Too vague — do NOT include these, return nothing instead

Be strict. Five high-quality promises are better than twenty mediocre ones."""


EXTRACT_PROMPT_TEMPLATE = """Extract trackable promises made by {leader_name} from the text below.

Source type: {source_type}
Source: {source_title}
Date: {source_date}

Text:
---
{text}
---

Rules:
- Only extract promises BY {leader_name}, not promises made by others or about others
- Each promise must have at least one concrete anchor (number, named scheme, specific deadline, or named beneficiary)
- Reject vague statements like "improve governance", "ensure development", "fight corruption"
- Reject past achievements — only forward-looking commitments count
- If the same promise appears multiple times in the text, extract it once

Return a JSON array. Each object must have:
{{
  "text": "verbatim quote or minimal paraphrase preserving all specifics",
  "summary": "one sentence, max 120 chars, must include the key specific detail (number/scheme/deadline)",
  "topic": one of ["economy", "healthcare", "education", "infrastructure", "agriculture", "defense", "environment", "social_welfare", "governance", "foreign_policy", "other"],
  "promised_at": "YYYY-MM-DD or null",
  "deadline": "YYYY-MM-DD if a deadline is mentioned, else null",
  "confidence_score": float 0.5–1.0 (see scoring guide — do not include below 0.5),
  "extraction_notes": "one line: which anchor makes this trackable (e.g. 'specific amount + deadline')"
}}

If no qualifying promises are found, return [].
Return ONLY valid JSON, no explanation."""


CONTRADICTION_PROMPT_TEMPLATE = """You are checking if a new political promise contradicts any existing promises by the same leader.

Leader: {leader_name}

New promise:
"{new_promise}"
(Made on: {new_date}, Topic: {topic})

Existing promises on the same topic:
{existing_promises}

For each existing promise that contradicts the new one, return a JSON array of objects:
{{
  "existing_promise_id": <id>,
  "explanation": "clear explanation of the contradiction",
  "severity": one of ["minor", "moderate", "major"],
  "confidence_score": float 0-1
}}

Contradiction criteria:
- Direct reversal: promised X, now promises not-X
- Incompatible commitments: two things that cannot both be true
- Timeline contradiction: promised 2 years, now says 5 years

NOT a contradiction:
- Refinements or additions to earlier promises
- Changed context (different geography, updated fiscal conditions)

Return [] if no contradictions. Return ONLY valid JSON."""


STATUS_CHECK_PROMPT_TEMPLATE = """You are reviewing whether a recent news article or parliamentary record indicates
a status change for any of the following existing political promises.

Leader: {leader_name}

Pending / in-progress promises:
{promises_list}

Recent article:
Title: {article_title}
Date: {article_date}
---
{article_text}
---

For each promise where this article clearly indicates a status change, return a JSON array:
{{
  "promise_id": <id>,
  "new_status": one of ["in_progress", "fulfilled", "broken", "expired"],
  "confidence_score": float 0-1,
  "note": "explanation of why this article indicates the status change",
  "evidence_quote": "relevant quote from the article (max 200 chars)"
}}

Rules:
- "fulfilled": concrete evidence the promise was delivered (inauguration, scheme launched, bill passed)
- "broken": leader explicitly walked back, or government action directly opposes the promise
- "in_progress": budget allocated, tender issued, bill introduced clearly related to this promise
- "expired": deadline passed with no action
- Only include promises where confidence_score >= 0.7
- Return [] if no status changes are evident

Return ONLY valid JSON, no other text."""


def _parse_json(raw: str) -> list:
    """Strip markdown fences and parse JSON, returning [] on failure."""
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else ""
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        result = json.loads(raw.strip())
        return result if isinstance(result, list) else []
    except json.JSONDecodeError as e:
        logger.error("JSON parse error: %s\nRaw: %.200s", e, raw)
        return []


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
        text=text[:6000],
    )
    raw = llm.complete(system=SYSTEM_PROMPT, user=prompt, max_tokens=2048)
    return _parse_json(raw)


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
    raw = llm.complete(system=SYSTEM_PROMPT, user=prompt, max_tokens=1024)
    return _parse_json(raw)


def check_promise_status(
    article_title: str,
    article_text: str,
    article_date: str | None,
    leader_name: str,
    existing_promises: list[dict],
) -> list[dict]:
    """Check if an article updates the status of any existing promises."""
    if not existing_promises:
        return []

    promises_list = "\n".join(
        f'[ID {p["id"]}] ({p["topic"]}) "{p["summary"]}" — status: {p["status"]}'
        for p in existing_promises
    )
    prompt = STATUS_CHECK_PROMPT_TEMPLATE.format(
        leader_name=leader_name,
        promises_list=promises_list,
        article_title=article_title,
        article_date=article_date or "unknown",
        article_text=article_text[:4000],
    )
    raw = llm.complete(system=SYSTEM_PROMPT, user=prompt, max_tokens=1024)
    return _parse_json(raw)
