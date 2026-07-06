"""Pure display helpers for the UI (framework-free, fully unit-testable).

The collector stores dates in mixed shapes — full ISO (`2026-06-10T00:00:00+00:00`),
date-only (`2026-06-16`), or NULL. `format_release` normalizes that into one human,
year-bearing string with a relative-age hint, e.g. `Jun 10, 2026 · 8d ago`.
"""

from __future__ import annotations

from datetime import date, datetime

from internshelper import clock

DASH = "—"


def _to_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _relative_age(days: int) -> str:
    """Coarse, date-granularity age. Empty string for future dates (no suffix)."""
    if days < 0:
        return ""
    if days == 0:
        return "today"
    if days <= 13:
        return f"{days}d ago"
    if days <= 59:
        return f"{days // 7}w ago"
    if days <= 729:
        return f"{days // 30}mo ago"
    return f"{days // 365}y ago"


def format_release(
    posted_at: str | None, first_seen: str | None = None, now: str | None = None
) -> str:
    """Human release date + relative age. Falls back to `first_seen`; `—` when unknown.

    `now` is a UTC ISO-8601 string (defaults to the real clock). Comparison is by calendar
    date only, so naive/aware tz differences in the inputs don't matter.
    """
    d = _to_date(posted_at) or _to_date(first_seen)
    if d is None:
        return DASH
    human = f"{d:%b} {d.day}, {d.year}"  # e.g. "Jun 10, 2026" (portable, no leading zero)
    ref = _to_date(now or clock.now_iso())
    age = _relative_age((ref - d).days) if ref else ""
    return f"{human} · {age}" if age else human
