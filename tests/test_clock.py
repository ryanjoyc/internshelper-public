from datetime import datetime, timezone

from internshelper.clock import now_iso, to_iso


def test_now_iso_is_parseable_aware_utc():
    s = now_iso()
    dt = datetime.fromisoformat(s)
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 0


def test_to_iso_epoch_seconds():
    assert to_iso(1761203288).startswith("2025-")  # GitHub date_posted (seconds)


def test_to_iso_epoch_millis():
    assert to_iso(1553186035299).startswith("2019-")  # Lever createdAt (milliseconds)


def test_to_iso_iso_with_offset_converted_to_utc():
    assert to_iso("2026-06-02T08:58:57-04:00") == "2026-06-02T12:58:57+00:00"


def test_to_iso_iso_with_ms_and_utc():
    assert to_iso("2026-04-07T17:12:35.753+00:00").startswith("2026-04-07T17:12:35")


def test_to_iso_date_only_passthrough():
    assert to_iso("2026-06-16") == "2026-06-16"


def test_to_iso_mon_dd_infers_current_year():
    now = datetime(2026, 6, 18, tzinfo=timezone.utc)
    assert to_iso("May 22", now=now) == "2026-05-22"


def test_to_iso_mon_dd_future_uses_prior_year():
    now = datetime(2026, 1, 5, tzinfo=timezone.utc)
    assert to_iso("Dec 22", now=now) == "2025-12-22"  # Dec 2026 would be far future → prior year


def test_to_iso_mon_dd_with_year():
    assert to_iso("May 22, 2025") == "2025-05-22"


def test_to_iso_garbage_and_empty_and_none():
    assert to_iso("not a date") is None
    assert to_iso("") is None
    assert to_iso(None) is None


# ---------- relative age ("5d", "3w", "2mo", "12h", "1y") ----------

_NOW = datetime(2026, 6, 18, tzinfo=timezone.utc)


def test_to_iso_rel_age_days():
    assert to_iso("5d", now=_NOW) == "2026-06-13"
    assert to_iso("18d", now=_NOW) == "2026-05-31"


def test_to_iso_rel_age_weeks():
    assert to_iso("3w", now=_NOW) == "2026-05-28"  # 21 days back


def test_to_iso_rel_age_months_approximate():
    assert to_iso("2mo", now=_NOW) == "2026-04-19"  # 60 days back (mo≈30d)


def test_to_iso_rel_age_hours_same_day():
    assert to_iso("12h", now=_NOW) == "2026-06-18"  # h collapses to same day


def test_to_iso_rel_age_years_approximate():
    assert to_iso("1y", now=_NOW) == "2025-06-18"  # 365 days back


def test_to_iso_rel_age_spaced_and_long_units():
    assert to_iso("5 days", now=_NOW) == "2026-06-13"
    assert to_iso("3 weeks", now=_NOW) == "2026-05-28"


def test_to_iso_rel_age_junk_is_none():
    assert to_iso("5x", now=_NOW) is None
    assert to_iso("d5", now=_NOW) is None
