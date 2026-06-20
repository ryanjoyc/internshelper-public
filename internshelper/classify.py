"""Classify a posting into is_internship / is_newgrad / is_cs_relevant flags.

A flag is true iff >=1 of its keywords matches (whole-word, case-insensitive)
anywhere in the concatenation of the title and the HTML-stripped description.

Keywords are config-driven (passed in). DEFAULT_KEYWORDS mirrors config/settings.toml
and is the baseline the labeled fixtures are pinned to.
"""

from __future__ import annotations

import re

from internshelper.models import Posting
from internshelper.text import strip_html

DEFAULT_KEYWORDS: dict[str, list[str]] = {
    "internship": ["intern", "interns", "internship", "co-op", "coop", "summer 20xx"],
    "newgrad": [
        "new grad", "new-grad", "newgrad", "entry level", "entry-level",
        "university grad", "early career",
    ],
    "cs": [
        "software", "swe", "sde", "developer", "engineer", "data", "machine learning",
        "ml", "ai", "quant", "backend", "frontend", "full stack", "fullstack",
        "infrastructure", "platform", "security", "embedded",
    ],
}

# Sentinel keyword -> a year-aware pattern. A blanket \bsummer 20\b would never match
# "Summer 2026" (the digit after 20 is a word char, so there is no boundary).
_SENTINELS = {"summer 20xx": r"\bsummer\s+20\d{2}\b"}


def _build_pattern(keyword: str) -> re.Pattern:
    key = keyword.strip().lower()
    if key in _SENTINELS:
        return re.compile(_SENTINELS[key], re.IGNORECASE)
    # Whole-word, whitespace-flexible for multi-word keywords.
    parts = [re.escape(p) for p in key.split()]
    body = r"\s+".join(parts)
    return re.compile(rf"\b{body}\b", re.IGNORECASE)


def compile_keywords(keywords: dict[str, list[str]]) -> dict[str, list[re.Pattern]]:
    """Precompile each flag's keyword list once, to reuse across many postings."""
    return {flag: [_build_pattern(k) for k in kws] for flag, kws in keywords.items()}


def _matches_any(text: str, patterns: list[re.Pattern]) -> bool:
    return any(p.search(text) for p in patterns)


def classify(posting: Posting, compiled: dict[str, list[re.Pattern]]) -> Posting:
    """Set the three flags on `posting` (in place) and return it."""
    text = f"{posting.title} {strip_html(posting.description)}"
    posting.is_internship = _matches_any(text, compiled.get("internship", []))
    posting.is_newgrad = _matches_any(text, compiled.get("newgrad", []))
    posting.is_cs_relevant = _matches_any(text, compiled.get("cs", []))
    return posting
