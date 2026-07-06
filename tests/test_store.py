import json
from pathlib import Path

import pytest

from internshelper import db, store
from internshelper.models import Posting


def _conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.init_db(c)
    return c


def _p(pid, source_key="greenhouse:stripe", title="Software Engineer Intern",
       cs=True, intern=True, newgrad=False):
    return Posting(
        posting_id=pid, source_key=source_key, title=title, company="Stripe",
        url=f"https://x/{pid}", location="Remote",
        is_cs_relevant=cs, is_internship=intern, is_newgrad=newgrad,
    )


def _row(conn, pid):
    return conn.execute("SELECT * FROM postings WHERE posting_id=?", (pid,)).fetchone()


# ---------- upsert ----------

def test_upsert_inserts_new_active_with_timestamps(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("greenhouse:1"), now="2026-06-18T10:00:00+00:00")
    r = _row(c, "greenhouse:1")
    assert r["is_active"] == 1
    assert r["first_seen"] == "2026-06-18T10:00:00+00:00"
    assert r["last_seen"] == "2026-06-18T10:00:00+00:00"
    assert r["source_type"] == "greenhouse"
    assert r["is_cs_relevant"] == 1 and r["is_internship"] == 1


def test_upsert_existing_keeps_first_seen_updates_last_seen_and_title(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("greenhouse:1", title="Old"), now="2026-06-18T10:00:00+00:00")
    store.upsert(c, _p("greenhouse:1", title="New"), now="2026-06-18T11:00:00+00:00")
    r = _row(c, "greenhouse:1")
    assert r["first_seen"] == "2026-06-18T10:00:00+00:00"
    assert r["last_seen"] == "2026-06-18T11:00:00+00:00"
    assert r["title"] == "New"


def test_upsert_writes_raw_payload_for_new_posting(tmp_path):
    c = _conn(tmp_path)
    payloads = tmp_path / "payloads"
    p = _p("greenhouse:1")
    p.raw = {"id": 1, "title": "X", "extra": "data"}
    store.upsert(c, p, now="2026-06-18T10:00:00+00:00", payloads_dir=payloads)
    row = _row(c, "greenhouse:1")
    assert row["review_status"] == "pending"
    assert row["payload_path"]
    assert json.loads(Path(row["payload_path"]).read_text()) == {"id": 1, "title": "X", "extra": "data"}


def test_upsert_update_preserves_review_state_and_original_payload(tmp_path):
    c = _conn(tmp_path)
    payloads = tmp_path / "payloads"
    p = _p("greenhouse:1")
    p.raw = {"v": 1}
    store.upsert(c, p, now="2026-06-18T10:00:00+00:00", payloads_dir=payloads)
    c.execute("UPDATE postings SET review_status='reviewed', verdict='match' WHERE posting_id='greenhouse:1'")
    c.commit()
    first_path = _row(c, "greenhouse:1")["payload_path"]

    p2 = _p("greenhouse:1", title="Changed")
    p2.raw = {"v": 2}
    store.upsert(c, p2, now="2026-06-18T11:00:00+00:00", payloads_dir=payloads)

    row = _row(c, "greenhouse:1")
    assert row["review_status"] == "reviewed"  # not reset by re-upsert
    assert row["verdict"] == "match"  # preserved
    assert row["title"] == "Changed"  # mutable field still updates
    assert row["payload_path"] == first_path  # payload not rewritten
    assert json.loads(Path(first_path).read_text()) == {"v": 1}  # original payload kept


def test_upsert_persists_posted_at(tmp_path):
    c = _conn(tmp_path)
    p = _p("greenhouse:1")
    p.posted_at = "2026-06-10T00:00:00+00:00"
    store.upsert(c, p, now="2026-06-18T10:00:00+00:00")
    assert _row(c, "greenhouse:1")["posted_at"] == "2026-06-10T00:00:00+00:00"


def test_upsert_without_payloads_dir_still_works(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("greenhouse:1"), now="2026-06-18T10:00:00+00:00")
    row = _row(c, "greenhouse:1")
    assert row["payload_path"] is None
    assert row["review_status"] == "pending"


def test_upsert_reactivates_a_closed_posting(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("greenhouse:1"), now="2026-06-18T10:00:00+00:00")
    c.execute("UPDATE postings SET is_active=0 WHERE posting_id='greenhouse:1'")
    c.commit()
    store.upsert(c, _p("greenhouse:1"), now="2026-06-18T12:00:00+00:00")
    assert _row(c, "greenhouse:1")["is_active"] == 1


# ---------- close detection ----------

def test_close_detection_closes_absent_when_source_succeeded(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("greenhouse:X"), now="2026-06-18T10:00:00+00:00")
    store.upsert(c, _p("greenhouse:Y"), now="2026-06-18T10:00:00+00:00")
    store.apply_close_detection(
        c, source_key="greenhouse:stripe", seen_ids={"greenhouse:X"}, ok=True, count=1,
    )
    assert _row(c, "greenhouse:X")["is_active"] == 1
    assert _row(c, "greenhouse:Y")["is_active"] == 0


def test_close_detection_does_nothing_when_source_errored(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("greenhouse:X"), now="2026-06-18T10:00:00+00:00")
    store.upsert(c, _p("greenhouse:Y"), now="2026-06-18T10:00:00+00:00")
    store.apply_close_detection(
        c, source_key="greenhouse:stripe", seen_ids=set(), ok=False, count=0,
    )
    assert _row(c, "greenhouse:X")["is_active"] == 1
    assert _row(c, "greenhouse:Y")["is_active"] == 1


def test_close_detection_does_nothing_on_empty_success(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("greenhouse:X"), now="2026-06-18T10:00:00+00:00")
    store.apply_close_detection(
        c, source_key="greenhouse:stripe", seen_ids=set(), ok=True, count=0,
    )
    assert _row(c, "greenhouse:X")["is_active"] == 1


def test_close_detection_is_scoped_to_source_key(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("greenhouse:X", source_key="greenhouse:stripe"), now="t")
    store.upsert(c, _p("lever:Z", source_key="lever:netflix"), now="t")
    store.apply_close_detection(
        c, source_key="greenhouse:stripe", seen_ids=set(), ok=True, count=5,
    )
    # The other source's posting is untouched even though it wasn't in seen_ids.
    assert _row(c, "lever:Z")["is_active"] == 1
    assert _row(c, "greenhouse:X")["is_active"] == 0


# ---------- runs + applications ----------

def test_record_run_and_read_back(tmp_path):
    c = _conn(tmp_path)
    store.record_run(c, source_key="greenhouse:stripe", ok=True, count=3,
                     error=None, now="2026-06-18T10:00:00+00:00", dropped=7)
    store.record_run(c, source_key="lever:netflix", ok=False, count=0,
                     error="boom", now="2026-06-18T10:00:01+00:00")
    rows = c.execute("SELECT source_key, ok, count, error, dropped FROM runs ORDER BY id").fetchall()
    assert (rows[0]["source_key"], rows[0]["ok"], rows[0]["count"]) == ("greenhouse:stripe", 1, 3)
    assert rows[0]["dropped"] == 7  # flood-guard drop count round-trips
    assert (rows[1]["ok"], rows[1]["error"]) == (0, "boom")
    assert rows[1]["dropped"] == 0  # defaults to 0 when not passed (pseudo/error rows)


def test_set_application_upserts(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("greenhouse:1"), now="t")
    store.set_application(c, "greenhouse:1", status="Applied", notes="ref X",
                          applied_date="2026-06-18")
    store.set_application(c, "greenhouse:1", status="Interviewing", notes="round 1",
                          applied_date="2026-06-18")
    r = c.execute("SELECT status, notes FROM applications WHERE posting_id='greenhouse:1'").fetchone()
    assert (r["status"], r["notes"]) == ("Interviewing", "round 1")


def test_set_application_rejects_unknown_status(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("greenhouse:1"), now="t")
    with pytest.raises(ValueError):
        store.set_application(c, "greenhouse:1", status="Ghosted")


def test_status_options_is_the_canonical_enum():
    assert store.STATUS_OPTIONS == (
        "Untracked", "Interested", "Applied", "Interviewing", "Rejected", "Offer"
    )


def test_get_application_returns_row_or_none(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("greenhouse:1"), now="t")
    assert store.get_application(c, "greenhouse:1") is None
    store.set_application(c, "greenhouse:1", status="Applied", notes="n",
                          applied_date="2026-06-18")
    row = store.get_application(c, "greenhouse:1")
    assert (row["status"], row["notes"], row["applied_date"]) == ("Applied", "n", "2026-06-18")


def test_set_application_status_preserves_notes_and_date(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("greenhouse:1"), now="t")
    store.set_application(c, "greenhouse:1", status="Applied", notes="ref X",
                          applied_date="2026-06-18")
    store.set_application_status(c, "greenhouse:1", "Interviewing")
    row = store.get_application(c, "greenhouse:1")
    assert row["status"] == "Interviewing"
    assert row["notes"] == "ref X"  # NOT clobbered by the status-only update
    assert row["applied_date"] == "2026-06-18"


def test_set_application_status_fills_date_only_when_empty(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("greenhouse:1"), now="t")
    # No row yet: the fill-in date lands.
    store.set_application_status(c, "greenhouse:1", "Applied",
                                 applied_date_if_empty="2026-07-01")
    assert store.get_application(c, "greenhouse:1")["applied_date"] == "2026-07-01"
    # Existing date: a later status change with a fill-in never overwrites it.
    store.set_application_status(c, "greenhouse:1", "Interviewing",
                                 applied_date_if_empty="2026-07-04")
    row = store.get_application(c, "greenhouse:1")
    assert row["status"] == "Interviewing"
    assert row["applied_date"] == "2026-07-01"


def test_set_application_status_creates_row_and_validates(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("greenhouse:1"), now="t")
    store.set_application_status(c, "greenhouse:1", "Applied")  # no prior row
    assert store.get_application(c, "greenhouse:1")["status"] == "Applied"
    with pytest.raises(ValueError):
        store.set_application_status(c, "greenhouse:1", "Ghosted")


def test_is_candidate_predicate():
    assert store.is_candidate({"is_cs_relevant": 1, "is_internship": 0, "is_newgrad": 0})
    assert store.is_candidate({"is_cs_relevant": 0, "is_internship": 1, "is_newgrad": 0})
    assert not store.is_candidate({"is_cs_relevant": 0, "is_internship": 0, "is_newgrad": 0})


def test_matches_with_status_left_joins_applications(tmp_path):
    from internshelper import review

    c = _conn(tmp_path)
    store.upsert(c, _p("greenhouse:1"), now="t1")
    store.upsert(c, _p("greenhouse:2"), now="t2")
    store.upsert(c, _p("greenhouse:3"), now="t3")
    review.set_verdict(c, "greenhouse:1", "match", "", now="2026-06-18T10:00:00+00:00")
    review.set_verdict(c, "greenhouse:2", "match", "", now="2026-06-18T11:00:00+00:00")
    review.set_verdict(c, "greenhouse:3", "no_match", "", now="2026-06-18T12:00:00+00:00")
    store.set_application(c, "greenhouse:2", status="Applied", notes="n",
                          applied_date="2026-06-18")

    rows = store.matches_with_status(c)
    by_id = {r["posting_id"]: r for r in rows}
    assert set(by_id) == {"greenhouse:1", "greenhouse:2"}  # no_match excluded
    assert by_id["greenhouse:1"]["status"] is None  # no application row -> NULL status
    assert by_id["greenhouse:2"]["status"] == "Applied"
    # Newest reviewed first
    assert [r["posting_id"] for r in rows] == ["greenhouse:2", "greenhouse:1"]


def test_feed_limit_offset_and_count(tmp_path):
    c = _conn(tmp_path)
    for i in range(5):
        store.upsert(c, _p(f"greenhouse:{i}"), now=f"2026-06-18T10:0{i}:00+00:00")
    kwargs = dict(require_cs=False, require_intern_or_newgrad=False,
                  include_all=True, include_closed=True)
    page = store.feed(c, **kwargs, limit=2, offset=1)
    # Newest first overall is greenhouse:4; offset 1 skips it.
    assert [r["posting_id"] for r in page] == ["greenhouse:3", "greenhouse:2"]
    assert store.feed_count(c, **kwargs) == 5
    assert store.feed_count(c, **kwargs, search="zzz") == 0
