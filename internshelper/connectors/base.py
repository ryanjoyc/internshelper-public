"""Connector base class, shared HTTP, and the type->connector registry.

Each connector turns one source entry into normalized Postings plus an explicit completeness
claim. Parsing is
split from fetching (`parse(data)` is pure) so it can be unit-tested against frozen
fixtures with no network. A blocked/slow source fails fast via the explicit timeout.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from internshelper.config import SourceEntry
from internshelper.models import Posting

# Bound a single attempt: 5s to connect, 20s to read. A stall raises (recorded ok=false)
# rather than hanging the hourly run.
TIMEOUT = httpx.Timeout(20.0, connect=5.0)
HEADERS = {"User-Agent": "internsHELPer/0.1 (personal job tracker)"}

_REGISTRY: dict[str, type["Connector"]] = {}


@dataclass(frozen=True)
class FetchResult:
    """Normalized postings plus whether the connector proved full enumeration."""

    postings: tuple[Posting, ...]
    complete: bool


def register(cls: type["Connector"]) -> type["Connector"]:
    _REGISTRY[cls.type] = cls
    return cls


def build_connector(entry: SourceEntry) -> "Connector":
    try:
        return _REGISTRY[entry.type](entry)
    except KeyError:
        raise ValueError(f"no connector registered for source type {entry.type!r}")


class Connector:
    type: str = ""

    def __init__(self, entry: SourceEntry) -> None:
        self.entry = entry
        # Human-readable notes explaining a degenerate/empty result (e.g. "couldn't map
        # required columns"). Surfaced at add-time so "0 postings" is never silent. Default
        # connectors leave this empty; populate it via `_warn` during fetch/parse.
        self.diagnostics: list[str] = []

    def _warn(self, message: str) -> None:
        """Record a diagnostic about the last fetch/parse (e.g. why a result was empty)."""
        self.diagnostics.append(message)

    @staticmethod
    def _declared_count(payload: dict, field: str) -> int | None:
        """Parse a non-negative first-page count without coercing ambiguous JSON values."""

        raw = payload.get(field)
        if isinstance(raw, bool):
            return None
        if isinstance(raw, int):
            return raw if raw >= 0 else None
        if isinstance(raw, str) and raw.strip().isdigit():
            return int(raw)
        return None

    def _pagination_complete(
        self,
        postings: list[Posting],
        *,
        fetched: int,
        expected: int | None,
        count_field: str,
    ) -> bool:
        """Prove exact, successfully normalized, unique offset pagination."""

        complete = True
        if expected is None:
            self._warn(
                f"first page omitted a valid {count_field!r} count; "
                "pagination completeness cannot be proved"
            )
            complete = False
        elif fetched != expected:
            self._warn(
                f"pagination returned {fetched} rows but first-page {count_field!r} "
                f"declared {expected}"
            )
            complete = False
        if len(postings) != fetched:
            complete = False
        unique_ids = {posting.posting_id for posting in postings}
        if len(unique_ids) != len(postings):
            self._warn(
                f"pagination returned {len(postings) - len(unique_ids)} duplicate "
                "stable posting id(s)"
            )
            complete = False
        return complete

    @property
    def source_key(self) -> str:
        return self.entry.source_key

    @property
    def company_fallback(self) -> str:
        """Company name to use when the source doesn't carry one (the board IS the company)."""
        return self.entry.label or self.entry.token

    def _get(self, url: str, params: dict | None = None):
        resp = httpx.get(
            url, params=params, timeout=TIMEOUT, headers=HEADERS, follow_redirects=True
        )
        resp.raise_for_status()
        return resp.json()

    def _post_json(self, url: str, body: dict):
        """POST a JSON body, return the JSON response (for POST-only APIs like Workday CXS)."""
        resp = httpx.post(
            url, json=body, timeout=TIMEOUT, headers=HEADERS, follow_redirects=True
        )
        resp.raise_for_status()
        return resp.json()

    def fetch(self) -> FetchResult:  # pragma: no cover - thin HTTP wrapper
        raise NotImplementedError

    def parse(self, data) -> list[Posting]:
        raise NotImplementedError
