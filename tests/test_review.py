import json

import pytest

from internshelper import config, db, review, store
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


def test_list_pending_search_filters_title_and_company(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:1", title="Quant Developer Intern", cs=True),
                 now="2026-06-18T10:00:00+00:00")
    store.upsert(c, _p("g:2", title="SWE Intern", cs=True), now="2026-06-18T10:01:00+00:00")
    c.execute("UPDATE postings SET company='Jane Street' WHERE posting_id='g:2'")
    c.commit()

    assert [r["posting_id"] for r in review.list_pending(c, q="QUANT")] == ["g:1"]  # title, ci
    assert [r["posting_id"] for r in review.list_pending(c, q="jane")] == ["g:2"]   # company
    assert review.list_pending(c, q="zzz") == []
    assert review.count_pending_filtered(c, q="quant") == 1
    assert review.count_pending_filtered(c) == 2


def test_list_pending_source_filter_and_options(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:1", cs=True), now="2026-06-18T10:00:00+00:00")
    store.upsert(c, _p("l:1", cs=True), now="2026-06-18T10:01:00+00:00")
    c.execute("UPDATE postings SET source_key='lever:x' WHERE posting_id='l:1'")
    c.commit()

    assert [r["posting_id"] for r in review.list_pending(c, source="lever:x")] == ["l:1"]
    assert review.pending_source_keys(c) == ["greenhouse:stripe", "lever:x"]
    # source_key is exposed on rows (the source pill needs it)
    keys = {r["posting_id"]: r["source_key"] for r in review.list_pending(c)}
    assert keys == {"g:1": "greenhouse:stripe", "l:1": "lever:x"}


def test_list_pending_sort_newest_ignores_candidate_tier(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:cand", cs=True), now="2026-06-18T09:00:00+00:00")
    store.upsert(c, _p("g:plain"), now="2026-06-18T10:00:00+00:00")
    c.execute("UPDATE postings SET posted_at='2026-06-01' WHERE posting_id='g:cand'")
    c.execute("UPDATE postings SET posted_at='2026-06-15' WHERE posting_id='g:plain'")
    c.commit()

    assert [r["posting_id"] for r in review.list_pending(c)] == ["g:cand", "g:plain"]
    assert [r["posting_id"] for r in review.list_pending(c, sort="newest")] == \
        ["g:plain", "g:cand"]


def test_list_pending_orders_by_rank_score_when_present(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:low", cs=True), now="2026-06-18T10:05:00+00:00")
    store.upsert(c, _p("g:high"), now="2026-06-18T10:00:00+00:00")
    store.upsert(c, _p("g:unscored", cs=True), now="2026-06-18T10:10:00+00:00")
    c.execute("UPDATE postings SET rank_score=0.2 WHERE posting_id='g:low'")
    c.execute("UPDATE postings SET rank_score=0.9 WHERE posting_id='g:high'")
    c.commit()  # g:unscored keeps NULL (e.g. collected mid-cycle, not yet rescored)

    ids = [r["posting_id"] for r in review.list_pending(c)]
    # score dominates the candidate flag; NULLs sort after scored rows
    assert ids == ["g:high", "g:low", "g:unscored"]
    assert review.list_pending(c)[0]["rank_score"] == 0.9
    assert "rank_reasons" in review.list_pending(c)[0]


def test_list_pending_all_null_scores_falls_back_to_heuristic(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:plain"), now="2026-06-18T10:05:00+00:00")
    store.upsert(c, _p("g:cand", cs=True), now="2026-06-18T10:00:00+00:00")
    ids = [r["posting_id"] for r in review.list_pending(c)]
    assert ids == ["g:cand", "g:plain"]  # exactly the pre-ranking order


def _guarded_entry(token="stripe", guard=("intern", "internship")):
    return config.SourceEntry(type="greenhouse", token=token, title_must_match=list(guard))


def test_find_guard_leaks_flags_only_current_guard_failures(tmp_path):
    c = _conn(tmp_path)
    entries = [_guarded_entry(), config.SourceEntry(type="lever", token="open")]  # open = no guard
    store.upsert(c, _p("g:leak", title="Internal Auditor - APAC"), now="2026-07-10T10:00:00+00:00")
    store.upsert(c, _p("g:ok", title="Software Engineer Intern"), now="2026-07-10T10:01:00+00:00")
    store.upsert(c, _p("g:gone", title="Internal Auditor"), now="2026-07-10T10:02:00+00:00")
    c.execute("UPDATE postings SET source_key='github:removed' WHERE posting_id='g:gone'")
    store.upsert(c, _p("g:free", title="Anything At All"), now="2026-07-10T10:03:00+00:00")
    c.execute("UPDATE postings SET source_key='lever:open' WHERE posting_id='g:free'")
    store.upsert(c, _p("g:applied", title="Internal Systems Lead"), now="2026-07-10T10:04:00+00:00")
    c.execute("INSERT INTO applications (posting_id, status) VALUES ('g:applied', 'Applied')")
    store.upsert(c, _p("g:done", title="Internal Ops"), now="2026-07-10T10:05:00+00:00")
    c.commit()
    review.set_verdict(c, "g:done", "no_match", "read", now="2026-07-10T11:00:00+00:00")

    leaks = review.find_guard_leaks(c, entries)
    assert [r["posting_id"] for r in leaks] == ["g:leak"]
    assert leaks[0]["title"] == "Internal Auditor - APAC"


def test_clear_guard_leaks_and_undo_round_trip(tmp_path):
    c = _conn(tmp_path)
    entries = [_guarded_entry()]
    # Real leaks are keyword-candidates (cs=True) — that's why "Clear non-candidates" misses them.
    store.upsert(c, _p("g:leak", title="Internal Auditor", cs=True), now="2026-07-10T10:00:00+00:00")
    store.upsert(c, _p("g:ok", title="SWE Intern", cs=True), now="2026-07-10T10:01:00+00:00")
    store.upsert(c, _p("g:plain", title="Chef"), now="2026-07-10T10:02:00+00:00")
    # A same-timestamp non-candidate bulk clear must not be reverted by the leak undo.
    review.clear_non_candidate_pending(c, now="2026-07-10T11:00:00+00:00")

    n = review.clear_guard_leaks(c, entries, now="2026-07-10T11:00:00+00:00")
    assert n == 1
    row = c.execute("SELECT verdict, verdict_reason, review_status FROM postings "
                    "WHERE posting_id='g:leak'").fetchone()
    assert row["verdict"] == "no_match"
    assert row["verdict_reason"] == review.GUARD_LEAK_REASON
    assert row["review_status"] == "reviewed"

    reverted = review.undo_bulk_clear(c, reviewed_at="2026-07-10T11:00:00+00:00",
                                      reason=review.GUARD_LEAK_REASON)
    assert reverted == 1
    pending = {r["posting_id"] for r in review.list_pending(c)}
    assert pending == {"g:leak", "g:ok"}  # g:plain stays in the other batch
    plain = c.execute("SELECT verdict_reason FROM postings WHERE posting_id='g:plain'").fetchone()
    assert plain["verdict_reason"] == review.BULK_CLEAR_REASON


def test_find_and_clear_closed_pending(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:open", title="SWE Intern"), now="2026-07-10T10:00:00+00:00")
    store.upsert(c, _p("g:closed", title="Data Intern"), now="2026-07-10T10:01:00+00:00")
    store.upsert(c, _p("g:closedapplied", title="Quant Intern"), now="2026-07-10T10:02:00+00:00")
    c.execute("UPDATE postings SET is_active=0 WHERE posting_id IN ('g:closed','g:closedapplied')")
    c.execute("INSERT INTO applications (posting_id, status) VALUES ('g:closedapplied', 'Applied')")
    c.commit()

    found = review.find_closed_pending(c)
    assert [r["posting_id"] for r in found] == ["g:closed"]

    n = review.clear_closed_pending(c, now="2026-07-10T11:00:00+00:00")
    assert n == 1
    row = c.execute("SELECT verdict, verdict_reason FROM postings "
                    "WHERE posting_id='g:closed'").fetchone()
    assert row["verdict"] == "no_match"
    assert row["verdict_reason"] == review.CLOSED_REASON
    applied_row = c.execute("SELECT review_status FROM postings "
                            "WHERE posting_id='g:closedapplied'").fetchone()
    assert applied_row["review_status"] == "pending"  # applied guard held


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


def test_cli_list_and_clear_leaks(tmp_path, capsys, monkeypatch):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:leak", title="Internal Auditor", cs=True),
                 now="2026-07-10T10:00:00+00:00")
    c.close()
    src = tmp_path / "sources.yaml"
    src.write_text("sources:\n  - type: greenhouse\n    token: stripe\n"
                   "    title_must_match: [intern, internship]\n")
    monkeypatch.setenv("INTERNSHELPER_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("INTERNSHELPER_SOURCES", str(src))

    assert review.main(["list-leaks"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [r["posting_id"] for r in out] == ["g:leak"]

    assert review.main(["clear-leaks"]) == 0
    assert "cleared 1" in capsys.readouterr().out
    c = db.connect(tmp_path / "t.db")
    row = c.execute("SELECT verdict_reason FROM postings WHERE posting_id='g:leak'").fetchone()
    assert row["verdict_reason"] == review.GUARD_LEAK_REASON


def test_cli_list_pending_emits_json(tmp_path, capsys, monkeypatch):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:1", cs=True), now="2026-06-18T10:00:00+00:00")
    c.close()
    monkeypatch.setenv("INTERNSHELPER_DB", str(tmp_path / "t.db"))
    rc = review.main(["list-pending"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out[0]["posting_id"] == "g:1"
