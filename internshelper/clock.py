"""Canonical time helpers: all timestamps are UTC ISO-8601 strings."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}")
_FORMATS_WITH_YEAR = ("%b %d, %Y", "%B %d, %Y")
_FORMATS_NO_YEAR = ("%b %d", "%B %d")


def now_iso() -> str:
    """Current time as a UTC ISO-8601 string (e.g. '2026-06-18T12:00:00+00:00')."""
    return datetime.now(timezone.utc).isoformat()


def to_iso(value, now: datetime | None = None) -> str | None:
    """Normalize a source's posted/added date to a UTC ISO-8601 string (or None).

    Handles: epoch int/float (>1e12 ⇒ milliseconds, else seconds); ISO-8601 strings (any
    offset → UTC); `YYYY-MM-DD` (kept date-only); `Mon DD` / `Mon DD, YYYY` (year inferred
    from `now` when absent — a date >7 days in the future is treated as the prior year).
    Anything unrecognized → None.
    """
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e12 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None

    s = str(value).strip()
    if not s:
        return None

    if _DATE_ONLY.fullmatch(s):
        return s

    try:  # ISO-8601 datetime
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        pass

    for fmt in _FORMATS_WITH_YEAR:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    ref = now if isinstance(now, datetime) else datetime.now(timezone.utc)
    for fmt in _FORMATS_NO_YEAR:  # parse with an explicit year to infer it (no leap-day warning)
        try:
            cand = datetime.strptime(f"{s} {ref.year}", f"{fmt} %Y")
        except ValueError:
            continue
        if cand.date() > ref.date() + timedelta(days=7):
            cand = cand.replace(year=ref.year - 1)
        return cand.strftime("%Y-%m-%d")
    return None
