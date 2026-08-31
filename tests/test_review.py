import json

import pytest

from internshelper import config, db, review, store
from internshelper.availability import SourceAuthority
from internshelper.availability_store import ensure_pending
from internshelper.models import Posting


def _conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.init_db(c)
    return c


def _p(pid, title="Role", cs=False, intern=False, newgrad=False):
    return Posting(posting_id=pid, source_key="greenhouse:stripe", title=title,
                   company="Stripe", url=f"https://x/{pid}",
                   is_cs_relevant=cs, is_internship=intern, is_newgrad=newgrad)


def test_list_inbox_orders_candidates_first(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:plain", cs=False), now="2026-06-18T10:00:00+00:00")
    store.upsert(c, _p("g:cand", cs=True, intern=True), now="2026-06-18T09:00:00+00:00")
    store.upsert(c, _p("g:reviewed", cs=True), now="2026-06-18T08:00:00+00:00")
    review.set_verdict(c, "g:reviewed", "no_match", "n/a", now="2026-06-18T11:00:00+00:00")

    rows = review.list_inbox(c)
    ids = [r["posting_id"] for r in rows]
    assert ids == ["g:cand", "g:plain"]  # candidate first; reviewed excluded
    assert "payload_path" in rows[0] and "url" in rows[0]


def test_list_inbox_limit(tmp_path):
    c = _conn(tmp_path)
    for i in range(5):
        store.upsert(c, _p(f"g:{i}", cs=True), now=f"2026-06-18T10:0{i}:00+00:00")
    assert len(review.list_inbox(c, limit=2)) == 2


def test_list_inbox_hides_availability_pending_rows(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:pending", cs=True), now="2026-06-18T10:00:00+00:00")
    ensure_pending(
        c,
        "g:pending",
        SourceAuthority.COMMUNITY_LIST,
        now="2026-06-18T10:01:00+00:00",
    )

    assert review.list_inbox(c) == []


def test_list_inbox_orders_by_recency_within_candidate_tier(tmp_path):
    c = _conn(tmp_path)
    for pid in ("g:old", "g:new", "g:none"):
        store.upsert(c, _p(pid, cs=True), now="2026-06-18T10:00:00+00:00")
    c.execute("UPDATE postings SET posted_at='2026-06-01' WHERE posting_id='g:old'")
    c.execute("UPDATE postings SET posted_at='2026-06-15' WHERE posting_id='g:new'")
    c.commit()  # g:none keeps posted_at NULL
    rows = review.list_inbox(c)
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


def test_list_inbox_exposes_first_seen(tmp_path):
    # Pending rows need first_seen for the same date fallback the dashboard uses elsewhere.
    c = _conn(tmp_path)
    store.upsert(c, _p("g:1", cs=True), now="2026-06-18T10:00:00+00:00")
    assert review.list_inbox(c)[0]["first_seen"] == "2026-06-18T10:00:00+00:00"


def test_list_inbox_offset_windows(tmp_path):
    c = _conn(tmp_path)
    for i in range(5):
        store.upsert(c, _p(f"g:{i}", cs=True), now=f"2026-06-18T10:0{i}:00+00:00")
    all_ids = [r["posting_id"] for r in review.list_inbox(c)]
    assert [r["posting_id"] for r in review.list_inbox(c, limit=2, offset=2)] == all_ids[2:4]
    # Offset without limit reaches the tail
    assert [r["posting_id"] for r in review.list_inbox(c, offset=3)] == all_ids[3:]


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


def test_undo_bulk_clear_reverts_only_the_batch(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:plain1"), now="2026-06-18T10:00:00+00:00")
    store.upsert(c, _p("g:plain2"), now="2026-06-18T10:01:00+00:00")
    store.upsert(c, _p("g:manual"), now="2026-06-18T10:02:00+00:00")
    review.set_verdict(c, "g:manual", "no_match", "read it", now="2026-06-18T10:30:00+00:00")
    # one batch = shared stamp + reason (how every bulk sweep writes its rows)
    for pid in ("g:plain1", "g:plain2"):
        review.set_verdict(c, pid, "no_match", "bulk: non-candidate", now="2026-06-18T11:00:00+00:00")

    reverted = review.undo_bulk_clear(
        c, reviewed_at="2026-06-18T11:00:00+00:00", reason="bulk: non-candidate"
    )

    assert reverted == 2
    pending = {r["posting_id"] for r in review.list_inbox(c)}
    assert pending == {"g:plain1", "g:plain2"}
    manual = c.execute(
        "SELECT verdict FROM postings WHERE posting_id='g:manual'"
    ).fetchone()
    assert manual["verdict"] == "no_match"  # individually-reviewed row untouched


def test_list_inbox_search_filters_title_and_company(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:1", title="Quant Developer Intern", cs=True),
                 now="2026-06-18T10:00:00+00:00")
    store.upsert(c, _p("g:2", title="SWE Intern", cs=True), now="2026-06-18T10:01:00+00:00")
    c.execute("UPDATE postings SET company='Jane Street' WHERE posting_id='g:2'")
    c.commit()

    assert [r["posting_id"] for r in review.list_inbox(c, q="QUANT")] == ["g:1"]  # title, ci
    assert [r["posting_id"] for r in review.list_inbox(c, q="jane")] == ["g:2"]   # company
    assert review.list_inbox(c, q="zzz") == []


def test_list_inbox_source_filter(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:1", cs=True), now="2026-06-18T10:00:00+00:00")
    store.upsert(c, _p("l:1", cs=True), now="2026-06-18T10:01:00+00:00")
    c.execute("UPDATE postings SET source_key='lever:x' WHERE posting_id='l:1'")
    c.commit()

    assert [r["posting_id"] for r in review.list_inbox(c, source="lever:x")] == ["l:1"]
    # source_key is exposed on rows (the source pill needs it)
    keys = {r["posting_id"]: r["source_key"] for r in review.list_inbox(c)}
    assert keys == {"g:1": "greenhouse:stripe", "l:1": "lever:x"}


def test_list_inbox_orders_by_rank_score_when_present(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:low", cs=True), now="2026-06-18T10:05:00+00:00")
    store.upsert(c, _p("g:high"), now="2026-06-18T10:00:00+00:00")
    store.upsert(c, _p("g:unscored", cs=True), now="2026-06-18T10:10:00+00:00")
    c.execute("UPDATE postings SET rank_score=0.2 WHERE posting_id='g:low'")
    c.execute("UPDATE postings SET rank_score=0.9 WHERE posting_id='g:high'")
    c.commit()  # g:unscored keeps NULL (e.g. collected mid-cycle, not yet rescored)

    ids = [r["posting_id"] for r in review.list_inbox(c)]
    # score dominates the candidate flag; NULLs sort after scored rows
    assert ids == ["g:high", "g:low", "g:unscored"]
    assert review.list_inbox(c)[0]["rank_score"] == 0.9
    assert "rank_reasons" in review.list_inbox(c)[0]


def test_list_inbox_all_null_scores_falls_back_to_heuristic(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:plain"), now="2026-06-18T10:05:00+00:00")
    store.upsert(c, _p("g:cand", cs=True), now="2026-06-18T10:00:00+00:00")
    ids = [r["posting_id"] for r in review.list_inbox(c)]
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
    store.upsert(c, _p("g:leak", title="Internal Auditor", cs=True), now="2026-07-10T10:00:00+00:00")
    store.upsert(c, _p("g:ok", title="SWE Intern", cs=True), now="2026-07-10T10:01:00+00:00")
    store.upsert(c, _p("g:plain", title="Chef"), now="2026-07-10T10:02:00+00:00")
    # A same-timestamp dismissal with another reason must not be reverted by the leak undo.
    review.dismiss(c, "g:plain", "not my field", now="2026-07-10T11:00:00+00:00")

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
    pending = {r["posting_id"] for r in review.list_inbox(c)}
    assert pending == {"g:leak", "g:ok"}  # g:plain stays in the other batch
    plain = c.execute("SELECT verdict_reason FROM postings WHERE posting_id='g:plain'").fetchone()
    assert plain["verdict_reason"] == "not my field"


def test_find_and_clear_closed_inbox(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:open", title="SWE Intern"), now="2026-07-10T10:00:00+00:00")
    store.upsert(c, _p("g:closed", title="Data Intern"), now="2026-07-10T10:01:00+00:00")
    store.upsert(c, _p("g:closedapplied", title="Quant Intern"), now="2026-07-10T10:02:00+00:00")
    c.execute("UPDATE postings SET is_active=0 WHERE posting_id IN ('g:closed','g:closedapplied')")
    c.execute("INSERT INTO applications (posting_id, status) VALUES ('g:closedapplied', 'Applied')")
    c.commit()

    found = review.find_closed_inbox(c)
    assert [r["posting_id"] for r in found] == ["g:closed"]

    n = review.clear_closed_inbox(c, now="2026-07-10T11:00:00+00:00")
    assert n == 1
    row = c.execute("SELECT verdict, verdict_reason FROM postings "
                    "WHERE posting_id='g:closed'").fetchone()
    assert row["verdict"] == "no_match"
    assert row["verdict_reason"] == review.CLOSED_REASON
    applied_row = c.execute("SELECT review_status FROM postings "
                            "WHERE posting_id='g:closedapplied'").fetchone()
    assert applied_row["review_status"] == "pending"  # applied guard held


def test_closed_hygiene_never_turns_availability_into_a_ranking_label(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:tracked", title="Data Intern"), now="2026-07-10T10:00:00+00:00")
    c.execute("UPDATE postings SET is_active=0 WHERE posting_id='g:tracked'")
    c.commit()
    ensure_pending(
        c,
        "g:tracked",
        SourceAuthority.FIRST_PARTY_ATS,
        now="2026-07-10T10:01:00+00:00",
    )

    assert review.find_closed_inbox(c) == []
    assert review.clear_closed_inbox(c, now="2026-07-10T11:00:00+00:00") == 0
    row = c.execute(
        "SELECT verdict, verdict_reason, review_status FROM postings "
        "WHERE posting_id='g:tracked'"
    ).fetchone()
    assert dict(row) == {
        "verdict": None,
        "verdict_reason": None,
        "review_status": "pending",
    }


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
    assert out["description"] == "x" * 100 + "…"


def test_payload_summary_unescapes_html_and_keeps_paragraphs(tmp_path):
    # Greenhouse ships `content` HTML-escaped; the summary must show clean
    # readable text — no tags — with paragraph breaks preserved.
    content = ("&lt;div&gt;&lt;p&gt;First &amp;amp; foremost.&lt;/p&gt;"
               "&lt;p&gt;Second.&lt;/p&gt;&lt;/div&gt;")
    p = tmp_path / "pay.json"
    p.write_text(json.dumps({"content": content}))
    out = review.payload_summary(str(p))
    assert out["description"] == "First & foremost.\n\nSecond."


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


def test_list_inbox_includes_matches_excludes_dismissed_and_pipeline(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:new", cs=True), now="2026-07-10T10:00:00+00:00")
    store.upsert(c, _p("g:match", cs=True), now="2026-07-10T10:01:00+00:00")
    store.upsert(c, _p("g:gone", cs=True), now="2026-07-10T10:02:00+00:00")
    store.upsert(c, _p("g:applied", cs=True), now="2026-07-10T10:03:00+00:00")
    store.upsert(c, _p("g:interested", cs=True), now="2026-07-10T10:04:00+00:00")
    review.set_verdict(c, "g:match", "match", "old gate", now="2026-07-10T11:00:00+00:00")
    review.dismiss(c, "g:gone", review.DISMISS_REASON, now="2026-07-10T11:01:00+00:00")
    store.set_application_status(c, "g:applied", "Applied")
    store.set_application_status(c, "g:interested", "Interested")

    ids = {r["posting_id"] for r in review.list_inbox(c)}
    # old matches and Interested stay in the inbox; dismissed + pipeline leave it
    assert ids == {"g:new", "g:match", "g:interested"}


def test_list_inbox_tier_filter_uses_company_group_not_legacy_pin(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:a", cs=True), now="2026-07-10T10:00:00+00:00")
    store.upsert(c, _p("g:b", cs=True), now="2026-07-10T10:01:00+00:00")
    c.execute("UPDATE postings SET tier='unclassified'")
    c.commit()
    review.pin_tier(c, "g:b", "top_target", now="2026-07-10T11:00:00+00:00")

    top = review.list_inbox(c, tier="top_target")
    assert top == []
    rest = review.list_inbox(c, tier="unclassified")
    assert {r["posting_id"] for r in rest} == {"g:a", "g:b"}
    assert next(r for r in rest if r["posting_id"] == "g:b")["pinned_tier"] == "top_target"


def test_dismiss_and_undo_round_trip(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:1", cs=True), now="2026-07-10T10:00:00+00:00")
    review.dismiss(c, "g:1", review.DISMISS_REASON, now="2026-07-10T11:00:00+00:00")
    row = c.execute("SELECT verdict, verdict_reason FROM postings WHERE posting_id='g:1'").fetchone()
    assert row["verdict"] == "no_match" and row["verdict_reason"] == review.DISMISS_REASON
    assert review.list_inbox(c) == []
    review.undo_dismiss(c, "g:1")
    assert [r["posting_id"] for r in review.list_inbox(c)] == ["g:1"]


def test_pin_tier_records_direction_and_unpin_clears(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:1", cs=True), now="2026-07-10T10:00:00+00:00")
    c.execute("UPDATE postings SET tier='unclassified'")
    c.commit()
    review.pin_tier(c, "g:1", "top_target", now="2026-07-10T11:00:00+00:00")
    row = c.execute("SELECT pinned_tier, tier_before_pin, pinned_at FROM postings").fetchone()
    assert (row["pinned_tier"], row["tier_before_pin"]) == ("top_target", "unclassified")
    assert row["pinned_at"] == "2026-07-10T11:00:00+00:00"
    # re-pinning records the previous EFFECTIVE tier as the new before
    review.pin_tier(c, "g:1", "known", now="2026-07-10T12:00:00+00:00")
    row = c.execute("SELECT pinned_tier, tier_before_pin FROM postings").fetchone()
    assert (row["pinned_tier"], row["tier_before_pin"]) == ("known", "top_target")
    review.unpin(c, "g:1")
    row = c.execute("SELECT pinned_tier, tier_before_pin, pinned_at FROM postings").fetchone()
    assert tuple(row) == (None, None, None)


def test_pin_tier_validates(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:1"), now="2026-07-10T10:00:00+00:00")
    with pytest.raises(ValueError, match="tier"):
        review.pin_tier(c, "g:1", "mega", now="t")
    with pytest.raises(ValueError, match="unknown posting"):
        review.pin_tier(c, "g:nope", "top_target", now="t")


def test_flag_parks_out_of_inbox_without_verdict(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:1", cs=True), now="2026-07-11T10:00:00+00:00")
    review.flag(c, "g:1", "link shows nothing", now="2026-07-11T11:00:00+00:00")

    row = c.execute("SELECT flagged_at, flag_reason, verdict FROM postings "
                    "WHERE posting_id='g:1'").fetchone()
    assert row["flagged_at"] == "2026-07-11T11:00:00+00:00"
    assert row["flag_reason"] == "link shows nothing"
    assert row["verdict"] is None  # a flag is not a preference signal
    assert review.list_inbox(c) == []
    assert [r["posting_id"] for r in review.list_flagged(c)] == ["g:1"]

    review.unflag(c, "g:1")
    assert [r["posting_id"] for r in review.list_inbox(c)] == ["g:1"]
    assert review.list_flagged(c) == []


def test_flag_defaults_reason_and_validates_posting(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:1"), now="2026-07-11T10:00:00+00:00")
    review.flag(c, "g:1", "", now="t")
    assert c.execute("SELECT flag_reason FROM postings WHERE posting_id='g:1'"
                     ).fetchone()[0] == review.FLAG_REASON_DEFAULT
    with pytest.raises(ValueError, match="unknown posting"):
        review.flag(c, "g:nope", "x", now="t")


def test_flag_keeps_hygiene_hands_off(tmp_path):
    # A flagged closed posting must NOT be swept by the closed-hygiene bulk dismiss —
    # it's parked for investigation, not eligible for cleanup.
    c = _conn(tmp_path)
    store.upsert(c, _p("g:1"), now="2026-07-11T10:00:00+00:00")
    c.execute("UPDATE postings SET is_active=0")
    c.commit()
    review.flag(c, "g:1", "dead link", now="t")
    assert review.find_closed_inbox(c) == []


def test_cli_flag_verbs(tmp_path, capsys, monkeypatch):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:1", title="Quant Intern", cs=True), now="2026-07-10T10:00:00+00:00")
    c.close()
    monkeypatch.setenv("INTERNSHELPER_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("INTERNSHELPER_COMPANY_GROUPS", str(tmp_path / "groups.yaml"))

    assert review.main(["flag", "g:1", "--reason", "careers page 404s"]) == 0
    assert "flagged for review" in capsys.readouterr().out
    assert review.main(["list-flagged"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["posting_id"] == "g:1"
    assert rows[0]["flag_reason"] == "careers page 404s"
    assert {"url", "source_key", "payload_path", "is_active"} <= set(rows[0])
    assert review.main(["list-inbox"]) == 0
    assert json.loads(capsys.readouterr().out) == []

    assert review.main(["unflag", "g:1"]) == 0
    assert "back in the inbox" in capsys.readouterr().out
    assert review.main(["list-flagged"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_cli_inbox_verbs(tmp_path, capsys, monkeypatch):
    c = _conn(tmp_path)
    store.upsert(c, _p("g:1", title="Quant Intern", cs=True), now="2026-07-10T10:00:00+00:00")
    c.execute("UPDATE postings SET tier='unclassified'")
    c.commit()
    c.close()
    monkeypatch.setenv("INTERNSHELPER_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("INTERNSHELPER_COMPANY_GROUPS", str(tmp_path / "groups.yaml"))

    assert review.main(["list-inbox"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["posting_id"] == "g:1" and "tier" in rows[0]

    assert review.main(["pin", "g:1", "--tier", "top_target"]) == 0
    assert "pinned to top_target" in capsys.readouterr().out
    assert review.main(["list-inbox", "--tier", "unclassified"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["posting_id"] == "g:1"
    assert review.main(["unpin", "g:1"]) == 0
    capsys.readouterr()

    assert review.main(["dismiss", "g:1", "--reason", "not my field"]) == 0
    assert "dismissed" in capsys.readouterr().out
    assert review.main(["list-inbox"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert review.main(["undo-dismiss", "g:1"]) == 0
    assert "back in the inbox" in capsys.readouterr().out
