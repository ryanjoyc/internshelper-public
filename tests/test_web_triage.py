"""Triage interactions: verdicts, undo, bulk clear, payload peek, focus mode.

Pending order in the seeded DB (candidates first, posting_id tiebreak):
greenhouse:1, greenhouse:2, greenhouse:plain.
"""

import sqlite3

from internshelper import db


def _row(db_path, pid):
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    row = c.execute("SELECT * FROM postings WHERE posting_id = ?", (pid,)).fetchone()
    c.close()
    return row


def _meta(db_path, key):
    c = db.connect(db_path)
    val = db.get_meta(c, key)
    c.close()
    return val


def test_verdict_match_writes_and_returns_undo_toast(client, seeded_db):
    r = client.post("/review/verdict",
                    data={"posting_id": "greenhouse:1", "verdict": "match"})
    assert r.status_code == 200
    row = _row(seeded_db, "greenhouse:1")
    assert row["verdict"] == "match"
    assert row["review_status"] == "reviewed"
    assert "/review/undo" in r.text  # the toast carries an undo affordance
    assert "hx-swap-oob" in r.text  # nav badges update out-of-band


def test_verdict_no_match_records_reason(client, seeded_db):
    r = client.post("/review/verdict",
                    data={"posting_id": "greenhouse:1", "verdict": "no_match",
                          "reason": "not a swe role"})
    assert r.status_code == 200
    row = _row(seeded_db, "greenhouse:1")
    assert row["verdict"] == "no_match"
    assert row["verdict_reason"] == "not a swe role"


def test_verdict_rejects_bad_value(client, seeded_db):
    r = client.post("/review/verdict",
                    data={"posting_id": "greenhouse:1", "verdict": "maybe"})
    assert r.status_code == 400
    assert _row(seeded_db, "greenhouse:1")["review_status"] == "pending"


def test_verdict_unknown_posting_is_404(client):
    r = client.post("/review/verdict",
                    data={"posting_id": "nope:1", "verdict": "match"})
    assert r.status_code == 404


def test_emptying_queue_resets_nudge_flag(client, seeded_db):
    c = db.connect(seeded_db)
    db.set_meta(c, "pending_notified", "1")
    c.close()
    for pid in ("greenhouse:1", "greenhouse:2", "greenhouse:plain"):
        assert client.post("/review/verdict",
                           data={"posting_id": pid, "verdict": "no_match"}).status_code == 200
    assert _meta(seeded_db, "pending_notified") == "0"


def test_undo_restores_pending(client, seeded_db):
    client.post("/review/verdict",
                data={"posting_id": "greenhouse:1", "verdict": "no_match", "reason": "oops"})
    r = client.post("/review/undo", data={"posting_id": "greenhouse:1", "mode": "list"})
    assert r.status_code == 200
    assert 'id="review-list"' in r.text  # the queue region re-renders
    row = _row(seeded_db, "greenhouse:1")
    assert row["review_status"] == "pending"
    assert row["verdict"] is None
    assert row["verdict_reason"] is None
    assert row["reviewed_at"] is None


def test_bulk_clear_then_undo(client, seeded_db):
    r = client.post("/review/bulk-clear")
    assert r.status_code == 200
    assert _row(seeded_db, "greenhouse:plain")["verdict"] == "no_match"
    assert _row(seeded_db, "greenhouse:1")["review_status"] == "pending"  # candidates kept
    assert "/review/bulk-undo" in r.text

    stamp = _row(seeded_db, "greenhouse:plain")["reviewed_at"]
    r2 = client.post("/review/bulk-undo",
                     data={"reviewed_at": stamp, "reason": "bulk: non-candidate"})
    assert r2.status_code == 200
    assert _row(seeded_db, "greenhouse:plain")["review_status"] == "pending"


def test_peek_renders_description_and_missing_branches(client):
    r = client.get("/review/peek/greenhouse:1")
    assert r.status_code == 200
    assert "Build backend systems" in r.text  # HTML-stripped description

    r2 = client.get("/review/peek/greenhouse:2")  # collected without a raw payload
    assert "No saved payload" in r2.text

    assert client.get("/review/peek/nope:1").status_code == 404


def test_focus_mode_shows_first_pending_with_progress(client):
    r = client.get("/review", params={"mode": "focus"})
    assert r.status_code == 200
    assert 'id="focus-card"' in r.text
    assert "1 of 3" in r.text
    assert "Build backend systems" in r.text  # greenhouse:1 is first, payload inline


def test_focus_verdict_advances_to_next_card(client, seeded_db):
    r = client.post("/review/verdict",
                    data={"posting_id": "greenhouse:1", "verdict": "match", "mode": "focus"})
    assert r.status_code == 200
    assert 'id="focus-card"' in r.text
    assert "1 of 2" in r.text  # queue shrank; still looking at the head
    assert _row(seeded_db, "greenhouse:1")["verdict"] == "match"


def test_focus_queue_clear_state(client):
    for pid in ("greenhouse:1", "greenhouse:2", "greenhouse:plain"):
        r = client.post("/review/verdict",
                        data={"posting_id": pid, "verdict": "no_match", "mode": "focus"})
    assert "Queue clear" in r.text
    assert "/board" in r.text


def _seed_leak(db_path, sources_path):
    """Guard stripe with intern/internship and add a pending leak-shaped candidate row."""
    sources_path.write_text(
        "sources:\n  - type: greenhouse\n    token: stripe\n"
        "    title_must_match: [intern, internship]\n"
    )
    c = sqlite3.connect(db_path)
    c.execute(
        "INSERT INTO postings (posting_id, source_key, source_type, title, company, url, "
        "is_cs_relevant, first_seen, last_seen, review_status) "
        "VALUES ('greenhouse:leak', 'greenhouse:stripe', 'greenhouse', "
        "'Internal Auditor - APAC', 'Stripe', 'https://x/leak', 1, "
        "'2026-07-10T10:00:00+00:00', '2026-07-10T10:00:00+00:00', 'pending')"
    )
    c.commit()
    c.close()


def test_review_page_shows_guard_leak_button(client, seeded_db, sources_file):
    # 2 leaks: the seeded "Line Cook" row shares source_key greenhouse:stripe, so the
    # new guard correctly flags it alongside the injected "Internal Auditor" row.
    _seed_leak(seeded_db, sources_file)
    r = client.get("/review")
    assert r.status_code == 200
    assert "Clear guard leaks (2)" in r.text


def test_bulk_clear_leaks_then_undo(client, seeded_db, sources_file):
    _seed_leak(seeded_db, sources_file)
    r = client.post("/review/bulk-clear-leaks")
    assert r.status_code == 200
    row = _row(seeded_db, "greenhouse:leak")
    assert row["verdict"] == "no_match"
    assert row["verdict_reason"] == "bulk: guard leak"
    assert _row(seeded_db, "greenhouse:plain")["verdict_reason"] == "bulk: guard leak"
    # Rows passing the guard stay pending.
    assert _row(seeded_db, "greenhouse:1")["review_status"] == "pending"
    assert "guard leak" in r.text and "/review/bulk-undo" in r.text

    r2 = client.post("/review/bulk-undo",
                     data={"reviewed_at": row["reviewed_at"], "reason": "bulk: guard leak"})
    assert r2.status_code == 200
    assert _row(seeded_db, "greenhouse:leak")["review_status"] == "pending"
    assert _row(seeded_db, "greenhouse:plain")["review_status"] == "pending"


def test_bulk_clear_closed_skips_applied(client, seeded_db):
    c = sqlite3.connect(seeded_db)
    c.execute("UPDATE postings SET is_active = 0 "
              "WHERE posting_id IN ('greenhouse:1', 'greenhouse:2')")
    c.execute("INSERT INTO applications (posting_id, status) VALUES ('greenhouse:2', 'Applied')")
    c.commit()
    c.close()

    r = client.get("/review")
    assert "Clear closed (1)" in r.text  # greenhouse:2 is applied — excluded

    r2 = client.post("/review/bulk-clear-closed")
    assert r2.status_code == 200
    assert _row(seeded_db, "greenhouse:1")["verdict_reason"] == "bulk: closed on source"
    assert _row(seeded_db, "greenhouse:2")["review_status"] == "pending"  # applied guard


def test_broken_sources_config_never_500s_review(client, seeded_db, sources_file):
    sources_file.write_text("sources: [this is: {not valid")
    assert client.get("/review").status_code == 200


def test_review_search_filters_rows_and_shows_note(client):
    r = client.get("/review", params={"q": "cook"})
    assert r.status_code == 200
    assert r.text.count('id="row-') == 1
    assert "greenhouse:plain" in r.text
    assert "1 shown of 3 pending" in r.text
    assert 'id="queue-toolbar"' in r.text


def test_review_filter_no_matches_shows_filtered_empty_state(client):
    r = client.get("/review", params={"q": "zzz"})
    assert r.status_code == 200
    assert "No pending postings match" in r.text
    assert "queue is clear" not in r.text


def test_review_source_filter_and_bad_sort_are_safe(client):
    r = client.get("/review", params={"source": "greenhouse:stripe", "sort": "bogus"})
    assert r.status_code == 200
    assert r.text.count('id="row-') == 3  # all seeded pending share the source


def test_bulk_clear_ignores_filter_scope(client, seeded_db):
    # Filters narrow the VIEW, never the bulk action's population.
    r = client.post("/review/bulk-clear", data={"q": "intern"})
    assert r.status_code == 200
    assert _row(seeded_db, "greenhouse:plain")["verdict"] == "no_match"  # didn't match q


def test_focus_mode_ignores_filters(client):
    r = client.get("/review", params={"mode": "focus", "q": "cook"})
    assert r.status_code == 200
    assert "1 of 3" in r.text  # global queue, not the filtered one


def _set_rank(db_path, pid, score, reasons='[["quant", 1.7], ["2026", -2.1]]'):
    c = sqlite3.connect(db_path)
    c.execute("UPDATE postings SET rank_score=?, rank_reasons=? WHERE posting_id=?",
              (score, reasons, pid))
    c.commit()
    c.close()


def test_scored_row_shows_reason_chips_and_pct_pill(client, seeded_db):
    _set_rank(seeded_db, "greenhouse:1", 0.87)
    r = client.get("/review")
    assert "likely match" not in r.text  # the one-size-fits-all badge is gone
    assert '"chip">quant</span>' in r.text  # positive reason chip
    assert "chip--down" not in r.text  # negative reasons stay off mid/high rows
    assert 'pill--rank" title="↑ quant · ↓ 2026">87<' in r.text


def test_low_tier_row_shows_negative_chips(client, seeded_db):
    _set_rank(seeded_db, "greenhouse:1", 0.12)
    r = client.get("/review")
    assert 'chip--down">↓ 2026</span>' in r.text  # why it's ranked low
    assert '"chip">quant</span>' not in r.text  # positive reasons stay off low rows


def test_reason_chip_humanizes_source_and_drops_company(client, seeded_db):
    _set_rank(
        seeded_db, "greenhouse:1", 0.5,
        reasons='[["company:stripe", 2.0], '
                '["source:markdown:https://raw.githubusercontent.com/o/summer-2027-internships/main/README.md", 1.1], '
                '["recent", 0.4]]',
    )
    r = client.get("/review")
    assert ">summer-2027-internships</span>" in r.text  # source:<url> -> short label
    assert ">new this week</span>" in r.text  # recent -> prose
    assert ">company:stripe</span>" not in r.text  # company chip dropped (redundant)


def test_unscored_rows_show_no_rank_pill(client):
    r = client.get("/review")
    assert "likely match" not in r.text
    assert "pill--rank" not in r.text
    assert 'class="chip' not in r.text


def test_tier_sections_render_with_bulk_buttons_and_summary(client, seeded_db):
    _set_rank(seeded_db, "greenhouse:1", 0.95)
    _set_rank(seeded_db, "greenhouse:2", 0.50)
    _set_rank(seeded_db, "greenhouse:plain", 0.10)
    r = client.get("/review")
    assert "tier--high" in r.text and "tier--mid" in r.text and "tier--low" in r.text
    assert "Near-certain matches" in r.text and "Needs your eyes" in r.text
    assert "Accept all" in r.text and "Dismiss all" in r.text
    assert "<strong>1 needs your call</strong>" in r.text
    assert "1 looks like a sure thing" in r.text
    assert "1 probably not" in r.text


def test_cold_start_uses_candidate_heuristic_without_bulk_buttons(client):
    r = client.get("/review")  # nothing scored in the seeded db
    assert "tier--mid" in r.text and "tier--low" in r.text
    assert "tier--high" not in r.text
    assert "Accept all" not in r.text and "Dismiss all" not in r.text
    assert "<strong>2 need your call</strong>" in r.text  # the two keyword candidates
    assert "1 probably not" in r.text  # Line Cook


def test_bulk_accept_then_undo(client, seeded_db):
    _set_rank(seeded_db, "greenhouse:1", 0.95)
    _set_rank(seeded_db, "greenhouse:2", 0.97)
    _set_rank(seeded_db, "greenhouse:plain", 0.10)
    r = client.post("/review/bulk-accept")
    assert r.status_code == 200
    assert _row(seeded_db, "greenhouse:1")["verdict"] == "match"
    assert _row(seeded_db, "greenhouse:2")["verdict"] == "match"
    assert _row(seeded_db, "greenhouse:plain")["review_status"] == "pending"
    assert "Accepted 2 sure things" in r.text and "/review/bulk-undo" in r.text

    stamp = _row(seeded_db, "greenhouse:1")["reviewed_at"]
    r2 = client.post("/review/bulk-undo",
                     data={"reviewed_at": stamp, "reason": "bulk: tier accept"})
    assert r2.status_code == 200
    assert _row(seeded_db, "greenhouse:1")["review_status"] == "pending"
    assert _row(seeded_db, "greenhouse:1")["verdict"] is None


def test_bulk_dismiss_is_filter_scoped_and_skips_unscored(client, seeded_db):
    _set_rank(seeded_db, "greenhouse:2", 0.20)  # scored low, but doesn't match q below
    _set_rank(seeded_db, "greenhouse:plain", 0.10)  # "Line Cook"
    # greenhouse:1 stays unscored — never bulk-dismissable.
    r = client.post("/review/bulk-dismiss", data={"q": "cook"})
    assert r.status_code == 200
    assert _row(seeded_db, "greenhouse:plain")["verdict"] == "no_match"
    assert _row(seeded_db, "greenhouse:plain")["verdict_reason"] == "bulk: tier dismiss"
    assert _row(seeded_db, "greenhouse:2")["review_status"] == "pending"  # filter scope
    assert _row(seeded_db, "greenhouse:1")["review_status"] == "pending"  # unscored guard


def test_bulk_dismiss_on_cold_start_touches_nothing(client, seeded_db):
    r = client.post("/review/bulk-dismiss")  # nothing scored
    assert r.status_code == 200
    for pid in ("greenhouse:1", "greenhouse:2", "greenhouse:plain"):
        assert _row(seeded_db, pid)["review_status"] == "pending"


def test_pane_view_renders_list_and_detail(client):
    r = client.get("/review", params={"mode": "pane"})
    assert r.status_code == 200
    assert 'id="pane-list"' in r.text and 'id="pane-detail"' in r.text
    assert r.text.count('<button class="pane-item') == 3
    assert "1 of 3" in r.text
    assert "Build backend systems" in r.text  # greenhouse:1 is first; payload inline
    assert 'name="mode" value="pane"' in r.text  # toolbar threads the view


def test_detail_description_is_clean_text_not_markup(client):
    # The seeded greenhouse:1 payload is HTML-escaped (Greenhouse-style); the
    # detail card must show readable lines and bullets, never literal tags.
    for r in (client.get("/review", params={"mode": "focus"}),
              client.get("/review/pane-card", params={"offset": 0})):
        assert r.status_code == 200
        assert "Build backend systems" in r.text
        assert "• Perk one" in r.text
        assert "&lt;div" not in r.text and "&lt;p&gt;" not in r.text


def test_pane_card_respects_offset_and_filters(client):
    r = client.get("/review/pane-card", params={"offset": 0, "q": "cook"})
    assert r.status_code == 200
    assert "Line Cook" in r.text
    assert "1 of 1" in r.text  # filtered list, not the global queue


def test_pane_verdict_advances_and_rerenders_region(client, seeded_db):
    r = client.post("/review/verdict",
                    data={"posting_id": "greenhouse:1", "verdict": "match", "mode": "pane"})
    assert r.status_code == 200
    assert 'id="pane-region"' in r.text
    assert "1 of 2" in r.text  # queue shrank; same offset = the next posting
    assert _row(seeded_db, "greenhouse:1")["verdict"] == "match"
    assert 'id="review-summary"' in r.text  # header sentence refreshed OOB


def test_pane_undo_lands_on_restored_posting(client, seeded_db):
    client.post("/review/verdict",
                data={"posting_id": "greenhouse:2", "verdict": "no_match", "mode": "pane"})
    r = client.post("/review/undo", data={"posting_id": "greenhouse:2", "mode": "pane"})
    assert r.status_code == 200
    assert 'id="pane-region"' in r.text
    assert "2 of 3" in r.text  # back at its position in pending order
    assert _row(seeded_db, "greenhouse:2")["review_status"] == "pending"


def test_view_cookie_remembers_last_mode(client):
    r = client.get("/review", params={"mode": "pane"})
    assert "review_view=pane" in r.headers.get("set-cookie", "")
    r2 = client.get("/review")  # bare URL — the TestClient jar carries the cookie
    assert 'id="pane-region"' in r2.text
    r3 = client.get("/review", params={"mode": "list"})  # explicit switch back wins
    assert 'id="review-list"' in r3.text
    assert client.get("/review").text.find('id="review-list"') != -1


def test_bad_mode_coerces_to_tiers(client):
    r = client.get("/review", params={"mode": "bogus"})
    assert r.status_code == 200
    assert 'id="review-list"' in r.text


def test_bulk_clear_from_pane_returns_pane_region(client, seeded_db):
    r = client.post("/review/bulk-clear", data={"mode": "pane"})
    assert r.status_code == 200
    assert 'id="pane-region"' in r.text
    assert _row(seeded_db, "greenhouse:plain")["verdict"] == "no_match"


def test_verdict_response_updates_summary_and_tier_counts_oob(client, seeded_db):
    _set_rank(seeded_db, "greenhouse:1", 0.95)
    r = client.post("/review/verdict",
                    data={"posting_id": "greenhouse:1", "verdict": "match"})
    assert r.status_code == 200
    assert 'id="review-summary" hx-swap-oob' in r.text
    assert 'id="tier-count-high" hx-swap-oob="outerHTML">0<' in r.text  # tier emptied


def test_focus_card_shows_score_why_line(client, seeded_db):
    _set_rank(seeded_db, "greenhouse:1", 0.87)
    r = client.get("/review", params={"mode": "focus"})
    assert "Score 87 —" in r.text and "↑ quant" in r.text


def test_verdict_triggers_rescore_of_remaining_pending(client, seeded_db):
    # Give the ranker enough interleaved strong history to train.
    c = sqlite3.connect(seeded_db)
    for i in range(20):
        c.execute(
            "INSERT INTO postings (posting_id, source_key, source_type, title, company, url, "
            "first_seen, last_seen, review_status, verdict, verdict_reason, reviewed_at) "
            f"VALUES ('h:m{i}', 'greenhouse:stripe', 'greenhouse', 'Quant Intern', 'C', 'u', "
            f"'t', 't', 'reviewed', 'match', 'yes', '2026-06-18T10:{i:02d}:00+00:00')")
    for i in range(15):
        c.execute(
            "INSERT INTO postings (posting_id, source_key, source_type, title, company, url, "
            f"first_seen, last_seen, review_status, verdict, verdict_reason, reviewed_at) "
            f"VALUES ('h:n{i}', 'greenhouse:stripe', 'greenhouse', 'Sales Manager', 'C', 'u', "
            f"'t', 't', 'reviewed', 'no_match', 'no', '2026-06-18T11:{i:02d}:00+00:00')")
    c.commit()
    c.close()

    r = client.post("/review/verdict",
                    data={"posting_id": "greenhouse:1", "verdict": "match"})
    assert r.status_code == 200
    # The remaining pending rows were rescored by the post-verdict retrain.
    assert _row(seeded_db, "greenhouse:2")["rank_score"] is not None


def test_source_pill_shows_short_label_not_the_raw_url(client, seeded_db):
    """Full-URL source_keys (markdown/workday) must render as a short name; the raw
    key survives only as the pill's hover title and the filter option's value."""
    key = "markdown:https://raw.githubusercontent.com/sndsh404/summer-2027-internships/main/README.md"
    c = sqlite3.connect(seeded_db)
    c.execute(
        "INSERT INTO postings (posting_id, source_key, source_type, title, company, url, "
        "is_cs_relevant, first_seen, last_seen, review_status) "
        "VALUES ('md:1', ?, 'markdown', 'Quant Intern', 'IMC', 'https://x/md1', 1, "
        "'2026-07-10T10:00:00+00:00', '2026-07-10T10:00:00+00:00', 'pending')",
        (key,),
    )
    c.commit()
    c.close()
    r = client.get("/review")
    assert r.status_code == 200
    assert ">summer-2027-internships</span>" in r.text  # short pill text
    assert f'title="{key}"' in r.text  # full key on hover
    assert f'value="{key}"' in r.text  # dropdown filters by the raw key
    assert f">{key}</span>" not in r.text  # never the raw URL as pill text
    assert ">stripe</span>" in r.text  # slug sources shortened too
