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


def test_cli_list_pending_emits_json(tmp_path, capsys, monkeypatch):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:1", cs=True), now="2026-06-18T10:00:00+00:00")
    c.close()
    monkeypatch.setenv("INTERNSHELPER_DB", str(tmp_path / "t.db"))
    rc = review.main(["list-pending"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out[0]["posting_id"] == "g:1"
