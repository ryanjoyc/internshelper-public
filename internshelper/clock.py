"""Canonical time helpers: all timestamps are UTC ISO-8601 strings."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}")
_FORMATS_WITH_YEAR = ("%b %d, %Y", "%B %d, %Y")
_FORMATS_NO_YEAR = ("%b %d", "%B %d")

# Relative-age strings ("5d", "18d", "3w", "2mo", "12h", "1y") as published by lists that date
# rows by how old they are rather than by an absolute date. Units map to a days-per-unit factor;
# `mo`/`y` are approximate and `h` collapses to "same day" (we keep date-only granularity).
_REL_AGE = re.compile(
    r"(\d+)\s*(hours?|hrs?|h|days?|d|weeks?|wks?|w|months?|mos?|mo|years?|yrs?|y)",
    re.IGNORECASE,
)
_REL_DAYS = {"h": 0, "d": 1, "w": 7, "mo": 30, "y": 365}  # h→same day; mo/y approximate


def _rel_unit_days(unit: str) -> int:
    unit = unit.lower()
    if unit.startswith("mo"):  # before "m"-less checks: month, mos, mo
        return _REL_DAYS["mo"]
    if unit.startswith("h"):
        return _REL_DAYS["h"]
    if unit.startswith("w"):
        return _REL_DAYS["w"]
    if unit.startswith("y"):
        return _REL_DAYS["y"]
    return _REL_DAYS["d"]


def now_iso() -> str:
    """Current time as a UTC ISO-8601 string (e.g. '2026-06-18T12:00:00+00:00')."""
    return datetime.now(timezone.utc).isoformat()


def to_iso(value, now: datetime | None = None) -> str | None:
    """Normalize a source's posted/added date to a UTC ISO-8601 string (or None).

    Handles: epoch int/float (>1e12 ⇒ milliseconds, else seconds); ISO-8601 strings (any
    offset → UTC); `YYYY-MM-DD` (kept date-only); relative ages (`5d`, `18d`, `3w`, `2mo`,
    `12h`, `1y`) resolved against `now` to a date-only `YYYY-MM-DD`; `Mon DD` / `Mon DD, YYYY`
    (year inferred from `now` when absent — a date >7 days in the future is treated as the
    prior year). Anything unrecognized → None.

    Relative-age note: "age" is days-since-posting as of the *source's* last refresh; we
    approximate it against our fetch `now`, so the derived date can be off by ~the source's
    refresh lag. Good enough for recency filtering ("last N days"); `first_seen` remains the
    authoritative "when we first saw it."
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

    m = _REL_AGE.fullmatch(s)
    if m:
        n = int(m.group(1))
        ref = now if isinstance(now, datetime) else datetime.now(timezone.utc)
        return (ref - timedelta(days=n * _rel_unit_days(m.group(2)))).strftime("%Y-%m-%d")

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
