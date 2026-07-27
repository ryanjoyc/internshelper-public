"""Normalized posting record shared across connectors, classify, and store."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Posting:
    """A single normalized job/internship posting.

    Connectors produce the scraped fields (posting_id, source_key, title, company,
    url, location, description). `classify` sets the three flags. Storage timestamps
    and `is_active` are managed by `store` against the DB, not carried here.

    `raw` is the source's original item dict (full payload). It is NOT a DB column —
    `store` writes it to disk once on first insert. Default None for hand-built objects.
    """

    posting_id: str
    source_key: str
    title: str
    company: str
    url: str
    location: str = ""
    description: str = ""
    is_internship: bool = False
    is_newgrad: bool = False
    is_cs_relevant: bool = False
    # v2.3: the source's own posted/added date (UTC ISO-8601 or date-only); None if unknown.
    posted_at: str | None = None
    # v3 term classifier: graded "is this my target term (e.g. Summer 2027)?" verdict.
    # These are NOT set by connectors — they are written by the on-demand term pass
    # (internshelper.term) via store.set_term, like review verdicts. Orthogonal to the
    # match/no_match review verdict: term = "which season/year", verdict = "do I want it".
    term_season: str | None = None          # "summer" | "fall" | "spring" | "winter" | None
    term_year: int | None = None            # e.g. 2027; None if no year is readable
    term_verdict: str | None = None         # one of TERM_VERDICTS; None = not yet classified
    term_evidence: str | None = None        # short quote/justification for the verdict
    term_source: str | None = None          # "heuristic" | "agent" | "api"
    term_classified_at: str | None = None   # UTC ISO-8601 when the term verdict was written
    raw: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    @property
    def source_type(self) -> str:
        """The source type prefix, derived from source_key (never set independently)."""
        return self.source_key.split(":", 1)[0]
