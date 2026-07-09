import json

import pytest

from internshelper import db, review, store
from internshelper.models import Posting


def _conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.init_db(c)
    return c


def _p(pid, title="Role", cs=False, intern=False, newgrad=False):
    return Posting(posting_id=pid, source_key="greenhouse:stripe", title=title,
                   company="Stripe", url=f"https://x/{pid}",
                   is_cs_relevant=cs, is_internship=intern, is_newgrad=newgrad)


def test_list_pending_orders_candidates_first(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:plain", cs=False), now="2026-06-18T10:00:00+00:00")
    store.upsert(c, _p("g:cand", cs=True, intern=True), now="2026-06-18T09:00:00+00:00")
    store.upsert(c, _p("g:reviewed", cs=True), now="2026-06-18T08:00:00+00:00")
    review.set_verdict(c, "g:reviewed", "no_match", "n/a", now="2026-06-18T11:00:00+00:00")

    rows = review.list_pending(c)
    ids = [r["posting_id"] for r in rows]
    assert ids == ["g:cand", "g:plain"]  # candidate first; reviewed excluded
    assert "payload_path" in rows[0] and "url" in rows[0]


def test_list_pending_limit(tmp_path):
    c = _conn(tmp_path)
    for i in range(5):
        store.upsert(c, _p(f"g:{i}", cs=True), now=f"2026-06-18T10:0{i}:00+00:00")
    assert len(review.list_pending(c, limit=2)) == 2


def test_list_pending_orders_by_recency_within_candidate_tier(tmp_path):
    c = _conn(tmp_path)
    for pid in ("g:old", "g:new", "g:none"):
        store.upsert(c, _p(pid, cs=True), now="2026-06-18T10:00:00+00:00")
    c.execute("UPDATE postings SET posted_at='2026-06-01' WHERE posting_id='g:old'")
    c.execute("UPDATE postings SET posted_at='2026-06-15' WHERE posting_id='g:new'")
    c.commit()  # g:none keeps posted_at NULL
    rows = review.list_pending(c)
    assert [r["posting_id"] for r in rows] == ["g:new", "g:old", "g:none"]  # newest first, null last
    assert "posted_at" in rows[0]


def test_set_verdict_writes_and_flips_status(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:1", cs=True), now="2026-06-18T10:00:00+00:00")
    review.set_verdict(c, "g:1", "match", "clear SWE intern", now="2026-06-18T11:00:00+00:00")
    row = c.execute("SELECT review_status, verdict, verdict_reason, reviewed_at "
                    "FROM postings WHERE posting_id='g:1'").fetchone()
    assert row["review_status"] == "reviewed"
    assert row["verdict"] == "match"
    assert row["verdict_reason"] == "clear SWE intern"
    assert row["reviewed_at"] == "2026-06-18T11:00:00+00:00"


def test_set_verdict_rejects_bad_value(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:1"), now="2026-06-18T10:00:00+00:00")
    with pytest.raises(ValueError):
        review.set_verdict(c, "g:1", "maybe", "x", now="t")


def test_finish_resets_notify_flag_only_when_no_pending(tmp_path):
    c = _conn(tmp_path)
    db.set_meta(c, "pending_notified", "1")
    store.upsert(c, _p("g:1", cs=True), now="2026-06-18T10:00:00+00:00")

    assert review.finish(c) is False  # still one pending
    assert db.get_meta(c, "pending_notified") == "1"

    review.set_verdict(c, "g:1", "match", "ok", now="2026-06-18T11:00:00+00:00")
    assert review.finish(c) is True  # all reviewed -> reset
    assert db.get_meta(c, "pending_notified") == "0"


def test_summary_returns_only_matches(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:1", cs=True), now="2026-06-18T10:00:00+00:00")
    store.upsert(c, _p("g:2", cs=True), now="2026-06-18T10:01:00+00:00")
    review.set_verdict(c, "g:1", "match", "yes", now="2026-06-18T11:00:00+00:00")
    review.set_verdict(c, "g:2", "no_match", "no", now="2026-06-18T11:00:00+00:00")
    matches = review.summary(c)
    assert [m["posting_id"] for m in matches] == ["g:1"]


def test_applied_lists_application_ids_with_names(tmp_path):
    # The deep-scan-source guard needs the set of already-applied posting_ids so it never
    # overwrites a verdict on a job the user has applied to.
    c = _conn(tmp_path)
    store.upsert(c, _p("g:1", title="SWE Intern", cs=True), now="2026-06-18T10:00:00+00:00")
    store.upsert(c, _p("g:2", title="Data Intern", cs=True), now="2026-06-18T10:01:00+00:00")
    c.execute("INSERT INTO applications (posting_id, status, applied_date) VALUES (?,?,?)",
              ("g:1", "Applied", "2026-07-01"))
    c.commit()
    rows = review.applied(c)
    assert [r["posting_id"] for r in rows] == ["g:1"]  # only the applied one
    assert rows[0]["company"] == "Stripe" and rows[0]["title"] == "SWE Intern"
    assert rows[0]["status"] == "Applied"


def test_applied_survives_missing_posting_row(tmp_path):
    # An application whose posting has been pruned must still be reported (guard must still skip it).
    c = _conn(tmp_path)
    c.execute("INSERT INTO applications (posting_id, status) VALUES (?,?)", ("gone:x", "Applied"))
    c.commit()
    rows = review.applied(c)
    assert [r["posting_id"] for r in rows] == ["gone:x"]
    assert rows[0]["company"] is None  # left join — no posting row, still listed


def test_summary_exposes_release_dates(tmp_path):
    # Confirmed matches must carry posted_at + first_seen so the dashboard can lead with a date.
    c = _conn(tmp_path)
    store.upsert(c, _p("g:1", cs=True), now="2026-06-18T10:00:00+00:00")
    c.execute("UPDATE postings SET posted_at='2026-06-10' WHERE posting_id='g:1'")
    c.commit()
    review.set_verdict(c, "g:1", "match", "yes", now="2026-06-18T11:00:00+00:00")
    row = review.summary(c)[0]
    assert row["posted_at"] == "2026-06-10"
    assert row["first_seen"] == "2026-06-18T10:00:00+00:00"


def test_list_pending_exposes_first_seen(tmp_path):
    # Pending rows need first_seen for the same date fallback the dashboard uses elsewhere.
    c = _conn(tmp_path)
    store.upsert(c, _p("g:1", cs=True), now="2026-06-18T10:00:00+00:00")
    assert review.list_pending(c)[0]["first_seen"] == "2026-06-18T10:00:00+00:00"


def test_list_pending_offset_windows(tmp_path):
    c = _conn(tmp_path)
    for i in range(5):
        store.upsert(c, _p(f"g:{i}", cs=True), now=f"2026-06-18T10:0{i}:00+00:00")
    all_ids = [r["posting_id"] for r in review.list_pending(c)]
    assert [r["posting_id"] for r in review.list_pending(c, limit=2, offset=2)] == all_ids[2:4]
    # Offset without limit reaches the tail
    assert [r["posting_id"] for r in review.list_pending(c, offset=3)] == all_ids[3:]


def test_reset_verdict_restores_pending(tmp_path):
    c = _conn(tmp_path)
    db.set_meta(c, "pending_notified", "0")
    store.upsert(c, _p("g:1", cs=True), now="2026-06-18T10:00:00+00:00")
    review.set_verdict(c, "g:1", "no_match", "oops", now="2026-06-18T11:00:00+00:00")

    review.reset_verdict(c, "g:1")

    row = c.execute("SELECT review_status, verdict, verdict_reason, reviewed_at "
                    "FROM postings WHERE posting_id='g:1'").fetchone()
    assert row["review_status"] == "pending"
    assert row["verdict"] is None
    assert row["verdict_reason"] is None
    assert row["reviewed_at"] is None
    # Undo never touches the nudge flag.
    assert db.get_meta(c, "pending_notified") == "0"


def test_clear_non_candidate_pending_is_atomic_and_scoped(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:cand", cs=True), now="2026-06-18T10:00:00+00:00")
    store.upsert(c, _p("g:plain1"), now="2026-06-18T10:01:00+00:00")
    store.upsert(c, _p("g:plain2"), now="2026-06-18T10:02:00+00:00")
    store.upsert(c, _p("g:done"), now="2026-06-18T10:03:00+00:00")
    review.set_verdict(c, "g:done", "match", "", now="2026-06-18T10:30:00+00:00")

    n = review.clear_non_candidate_pending(c, now="2026-06-18T11:00:00+00:00")

    assert n == 2
    rows = {r["posting_id"]: r for r in c.execute(
        "SELECT posting_id, review_status, verdict, verdict_reason, reviewed_at FROM postings"
    )}
    assert rows["g:cand"]["review_status"] == "pending"  # candidates untouched
    assert rows["g:done"]["verdict"] == "match"  # already-reviewed untouched
    for pid in ("g:plain1", "g:plain2"):
        assert rows[pid]["verdict"] == "no_match"
        assert rows[pid]["verdict_reason"] == "bulk: non-candidate"
        assert rows[pid]["reviewed_at"] == "2026-06-18T11:00:00+00:00"  # shared batch stamp


def test_undo_bulk_clear_reverts_only_the_batch(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:plain1"), now="2026-06-18T10:00:00+00:00")
    store.upsert(c, _p("g:plain2"), now="2026-06-18T10:01:00+00:00")
    store.upsert(c, _p("g:manual"), now="2026-06-18T10:02:00+00:00")
    review.set_verdict(c, "g:manual", "no_match", "read it", now="2026-06-18T10:30:00+00:00")
    n = review.clear_non_candidate_pending(c, now="2026-06-18T11:00:00+00:00")
    assert n == 2

    reverted = review.undo_bulk_clear(
        c, reviewed_at="2026-06-18T11:00:00+00:00", reason="bulk: non-candidate"
    )

    assert reverted == 2
    pending = {r["posting_id"] for r in review.list_pending(c)}
    assert pending == {"g:plain1", "g:plain2"}
    manual = c.execute(
        "SELECT verdict FROM postings WHERE posting_id='g:manual'"
    ).fetchone()
    assert manual["verdict"] == "no_match"  # individually-reviewed row untouched


def test_payload_summary_extracts_and_strips_description(tmp_path):
    p = tmp_path / "pay.json"
    p.write_text(json.dumps({"content": "<p>Build <b>backend</b> systems</p>", "id": 7}))
    out = review.payload_summary(str(p))
    assert out["state"] == "ok"
    assert out["description"] == "Build backend systems"
    assert out["raw"] == {"content": "<p>Build <b>backend</b> systems</p>", "id": 7}


def test_payload_summary_truncates_to_max_chars(tmp_path):
    p = tmp_path / "pay.json"
    p.write_text(json.dumps({"description": "x" * 5000}))
    out = review.payload_summary(str(p), max_chars=100)
    assert len(out["description"]) == 100


def test_payload_summary_missing_and_unreadable(tmp_path):
    assert review.payload_summary(None)["state"] == "missing"
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert review.payload_summary(str(bad))["state"] == "unreadable"


def test_cli_list_pending_emits_json(tmp_path, capsys, monkeypatch):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:1", cs=True), now="2026-06-18T10:00:00+00:00")
    c.close()
    monkeypatch.setenv("INTERNSHELPER_DB", str(tmp_path / "t.db"))
    rc = review.main(["list-pending"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out[0]["posting_id"] == "g:1"
