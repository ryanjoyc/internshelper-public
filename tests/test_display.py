"""Unit tests for the pure date-display formatter used by the dashboard Feed.

`format_release` turns a posting's messy `posted_at` (full ISO / date-only / None) into a
human, year-bearing string with a relative-age hint, e.g. "Jun 10, 2026 · 8d ago".
"""

from internshelper.display import format_release

NOW = "2026-06-18T10:00:00+00:00"


def test_full_iso_with_explicit_now():
    assert format_release("2026-06-10T00:00:00+00:00", now=NOW) == "Jun 10, 2026 · 8d ago"


def test_date_only_input():
    assert format_release("2026-06-16", now=NOW) == "Jun 16, 2026 · 2d ago"


def test_today_reads_today():
    assert format_release("2026-06-18", now=NOW) == "Jun 18, 2026 · today"


def test_year_always_present():
    out = format_release("2026-01-05", now=NOW)
    assert "2026" in out
    assert out == "Jan 5, 2026 · 5mo ago"


def test_no_leading_zero_on_day():
    assert format_release("2026-06-05", now=NOW) == "Jun 5, 2026 · 13d ago"


def test_falls_back_to_first_seen_when_posted_none():
    assert format_release(None, first_seen="2026-06-18T09:00:00+00:00", now=NOW) == "Jun 18, 2026 · today"


def test_posted_at_wins_over_first_seen():
    out = format_release("2026-06-10", first_seen="2026-06-18T09:00:00+00:00", now=NOW)
    assert out == "Jun 10, 2026 · 8d ago"


def test_both_none_returns_dash():
    assert format_release(None, None) == "—"


def test_unparseable_returns_dash():
    assert format_release("not a date", now=NOW) == "—"


def test_future_date_has_no_age_suffix():
    assert format_release("2026-06-25", now=NOW) == "Jun 25, 2026"


def test_boundary_13_days_is_days():
    assert format_release("2026-06-05", now=NOW) == "Jun 5, 2026 · 13d ago"


def test_boundary_14_days_is_weeks():
    assert format_release("2026-06-04", now=NOW) == "Jun 4, 2026 · 2w ago"


def test_weeks_bucket():
    assert format_release("2026-05-28", now=NOW) == "May 28, 2026 · 3w ago"


def test_months_bucket():
    assert format_release("2026-03-20", now=NOW) == "Mar 20, 2026 · 3mo ago"


def test_years_bucket():
    assert format_release("2024-06-18", now=NOW) == "Jun 18, 2024 · 2y ago"


def test_default_now_does_not_crash():
    # No explicit `now` → uses the real clock; just assert it produces a dated string.
    out = format_release("2020-01-15")
    assert out.startswith("Jan 15, 2020")
