from internshelper import db, store
from internshelper.models import Posting


def _conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.init_db(c)
    return c


def _p(pid, title="SWE Intern", company="Stripe", cs=True, intern=True, newgrad=False):
    return Posting(posting_id=pid, source_key="greenhouse:stripe", title=title,
                   company=company, url=f"https://x/{pid}", location="NYC",
                   is_cs_relevant=cs, is_internship=intern, is_newgrad=newgrad)


# ---------- feed ----------

def test_feed_default_shows_active_matching_only(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:match"), now="2026-06-18T10:00:00+00:00")
    store.upsert(c, _p("g:csonly", intern=False, newgrad=False), now="2026-06-18T10:01:00+00:00")
    store.upsert(c, _p("g:closed"), now="2026-06-18T10:02:00+00:00")
    c.execute("UPDATE postings SET is_active=0 WHERE posting_id='g:closed'"); c.commit()

    ids = [r["posting_id"] for r in store.feed(c, require_cs=True, require_intern_or_newgrad=True)]
    assert ids == ["g:match"]


def test_feed_include_all_ignores_filter(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:match"), now="2026-06-18T10:00:00+00:00")
    store.upsert(c, _p("g:csonly", intern=False, newgrad=False), now="2026-06-18T10:01:00+00:00")
    ids = {r["posting_id"] for r in store.feed(c, require_cs=True, require_intern_or_newgrad=True,
                                               include_all=True)}
    assert ids == {"g:match", "g:csonly"}


def test_feed_search_matches_title_or_company(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:1", title="Backend Intern"), now="2026-06-18T10:00:00+00:00")
    store.upsert(c, _p("g:2", title="Frontend Intern"), now="2026-06-18T10:01:00+00:00")
    ids = [r["posting_id"] for r in store.feed(c, require_cs=True, require_intern_or_newgrad=True,
                                               search="backend")]
    assert ids == ["g:1"]


# ---------- tracker ----------

def test_tracker_returns_only_postings_with_an_application(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:1"), now="2026-06-18T10:00:00+00:00")
    store.upsert(c, _p("g:2"), now="2026-06-18T10:01:00+00:00")
    store.set_application(c, "g:1", status="Applied", notes="n", applied_date="2026-06-18")
    rows = store.tracker(c)
    assert [r["posting_id"] for r in rows] == ["g:1"]
    assert rows[0]["status"] == "Applied"
    assert rows[0]["title"] == "SWE Intern"


# ---------- health ----------

def test_health_returns_latest_run_per_source(tmp_path):
    c = _conn(tmp_path)
    store.record_run(c, "greenhouse:stripe", ok=True, count=5, error=None, now="2026-06-18T10:00:00+00:00")
    store.record_run(c, "greenhouse:stripe", ok=False, count=0, error="boom", now="2026-06-18T11:00:00+00:00")
    store.record_run(c, "lever:netflix", ok=True, count=2, error=None, now="2026-06-18T10:30:00+00:00")
    rows = {r["source_key"]: r for r in store.health(c)}
    assert set(rows) == {"greenhouse:stripe", "lever:netflix"}
    assert rows["greenhouse:stripe"]["ok"] == 0  # the later (failed) run
    assert rows["greenhouse:stripe"]["error"] == "boom"
    assert rows["lever:netflix"]["count"] == 2


# ---------- source_health: 'went quiet' detection ----------

def _runs(c, source_key, counts, ok=True):
    """Record successful runs with the given counts in chronological order."""
    for i, n in enumerate(counts):
        store.record_run(c, source_key, ok=ok, count=n, error=None,
                         now=f"2026-06-18T10:{i:02d}:00+00:00")


def _by_key(rows):
    return {r["source_key"]: r for r in rows}


def test_source_health_flags_zero_after_nonzero(tmp_path):
    c = _conn(tmp_path)
    _runs(c, "greenhouse:stripe", [5, 5, 5, 5, 0])  # was producing, now silent
    row = _by_key(store.source_health(c))["greenhouse:stripe"]
    assert row["quiet"] is True
    assert "went quiet" in row["quiet_reason"]


def test_source_health_flags_sharp_drop(tmp_path):
    c = _conn(tmp_path)
    _runs(c, "greenhouse:stripe", [10, 10, 10, 10, 3])  # 3 < 0.5 * baseline(10)
    row = _by_key(store.source_health(c))["greenhouse:stripe"]
    assert row["quiet"] is True
    assert "sharp drop" in row["quiet_reason"]


def test_source_health_steady_source_not_flagged(tmp_path):
    c = _conn(tmp_path)
    _runs(c, "greenhouse:stripe", [5, 5, 5, 5, 5])
    row = _by_key(store.source_health(c))["greenhouse:stripe"]
    assert row["quiet"] is False and row["quiet_reason"] == ""


def test_source_health_low_volume_not_flagged(tmp_path):
    c = _conn(tmp_path)
    _runs(c, "greenhouse:stripe", [2, 2, 2, 0])  # baseline 2 < floor 3 -> no false positive
    row = _by_key(store.source_health(c))["greenhouse:stripe"]
    assert row["quiet"] is False


def test_source_health_pseudo_rows_never_flagged(tmp_path):
    c = _conn(tmp_path)
    _runs(c, "run", [5, 5, 5, 0])  # heartbeat 'run' has no ':' -> not a board
    row = _by_key(store.source_health(c))["run"]
    assert row["quiet"] is False


def test_source_health_insufficient_history_not_flagged(tmp_path):
    c = _conn(tmp_path)
    _runs(c, "greenhouse:stripe", [0])  # single run, nothing to compare against
    row = _by_key(store.source_health(c))["greenhouse:stripe"]
    assert row["quiet"] is False


def test_source_health_surfaces_dropped_and_error(tmp_path):
    c = _conn(tmp_path)
    store.record_run(c, "greenhouse:stripe", ok=True, count=4, error=None,
                     now="2026-06-18T10:00:00+00:00", dropped=6)
    row = _by_key(store.source_health(c))["greenhouse:stripe"]
    assert row["count"] == 4 and row["dropped"] == 6
