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
    raw: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    @property
    def source_type(self) -> str:
        """The source type prefix, derived from source_key (never set independently)."""
        return self.source_key.split(":", 1)[0]
