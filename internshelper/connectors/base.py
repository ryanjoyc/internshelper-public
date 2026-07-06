"""Connector base class, shared HTTP, and the type->connector registry.

Each connector turns one source entry into a list of normalized Postings. Parsing is
split from fetching (`parse(data)` is pure) so it can be unit-tested against frozen
fixtures with no network. A blocked/slow source fails fast via the explicit timeout.
"""

from __future__ import annotations

import httpx

from internshelper.config import SourceEntry
from internshelper.models import Posting

# Bound a single attempt: 5s to connect, 20s to read. A stall raises (recorded ok=false)
# rather than hanging the hourly run.
TIMEOUT = httpx.Timeout(20.0, connect=5.0)
HEADERS = {"User-Agent": "internsHELPer/0.1 (personal job tracker)"}

_REGISTRY: dict[str, type["Connector"]] = {}


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

    def fetch(self) -> list[Posting]:  # pragma: no cover - thin HTTP wrapper
        raise NotImplementedError

    def parse(self, data) -> list[Posting]:
        raise NotImplementedError
