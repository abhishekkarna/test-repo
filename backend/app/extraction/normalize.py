"""
Normalize Indian and international number expressions to plain integers
before embedding, so "5 lakh homes" and "500,000 homes" produce similar vectors.

Examples:
  "₹2,000 crore"  → "₹20000000000"
  "5 lakh farmers" → "500000 farmers"
  "Rs. 10,000 cr"  → "Rs. 100000000000"
  "1,00,000 units" → "100000 units"
  "3 million jobs" → "3000000 jobs"
"""

import re

_UNIT_MULTIPLIERS = {
    "lakh": 100_000,
    "lakhs": 100_000,
    "lac": 100_000,
    "lacs": 100_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
    "cr": 10_000_000,
    "thousand": 1_000,
    "thousands": 1_000,
    "million": 1_000_000,
    "millions": 1_000_000,
    "billion": 1_000_000_000,
    "billions": 1_000_000_000,
}

# Matches optional currency prefix, a number, then a unit word
_PATTERN = re.compile(
    r"(₹|Rs\.?\s*|INR\s*)?"
    r"(\d+(?:\.\d+)?)\s*"
    r"(lakh|lakhs|lacs|lac|crore|crores|cr|million|millions|billion|billions|thousand|thousands)"
    r"\b",
    re.IGNORECASE,
)

# Indian-style comma separators: 1,00,000 or 10,00,000
_INDIAN_COMMA = re.compile(r"(\d),(\d{2}),(\d{3})")
_WESTERN_COMMA = re.compile(r"(\d),(\d{3})(?!\d)")


def normalize_numbers(text: str) -> str:
    """Return text with Indian/international number expressions replaced by plain integers."""
    # Strip Indian comma formatting first (must run before western comma strip)
    text = _INDIAN_COMMA.sub(r"\1\2\3", text)
    text = _WESTERN_COMMA.sub(r"\1\2", text)

    def _replace(m: re.Match) -> str:
        currency = m.group(1) or ""
        num = float(m.group(2))
        unit = m.group(3).lower()
        result = int(num * _UNIT_MULTIPLIERS[unit])
        return f"{currency}{result}"

    return _PATTERN.sub(_replace, text)
