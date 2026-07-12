"""The merged Board: Inbox tier sections, dismiss/pin flows, table lens, dismissed view.

Seeded population (conftest): four inbox postings — greenhouse:0 (old match verdict),
greenhouse:1 + greenhouse:2 (candidates), greenhouse:plain (non-candidate, company
Bistro). No application rows, no rank scores (cold start).
"""

import sqlite3

from internshelper import db, review, store


def _exec(db_path, sql, params=()):
    c = sqlite3.connect(db_path)
    c.execute(sql, params)
    c.commit()
    c.close()


def _posting(db_path, pid):
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    row = c.execute("SELECT * FROM postings WHERE posting_id = ?", (pid,)).fetchone()
    c.close()
    return row


def test_board_groups_inbox_into_tier_sections(client):
    r = client.get("/board")
    assert r.status_code == 200
    # all four tier sections render (they are drop targets even when empty)
    for key in ("apply_first", "target", "everything_else", "long_shots"):
        assert f'data-tier="{key}"' in r.text
    assert "Apply first" in r.text and "Long shots" in r.text
    assert "Software Engineer Intern" in r.text
    assert "Line Cook" in r.text


def test_cold_start_tiers_dream_candidates_vs_junk(client, seeded_db):
    client.get("/board")  # triggers the self-heal retier
    # Stripe is a built-in dream company, so its cold-start candidates go straight up;
    # the non-candidate (Bistro's Line Cook) is buried regardless of company.
    assert _posting(seeded_db, "greenhouse:1")["tier"] == "apply_first"
    assert _posting(seeded_db, "greenhouse:plain")["tier"] == "long_shots"


def test_dream_company_lands_in_apply_first(client, seeded_db):
    # Stripe is on the built-in dream list; a rescored row with a decent score goes top.
    _exec(seeded_db, "UPDATE postings SET rank_score = 0.8, tier = NULL")
    r = client.get("/board")
    assert _posting(seeded_db, "greenhouse:1")["tier"] == "apply_first"
    # Bistro is unlisted -> everything_else once scored
    assert _posting(seeded_db, "greenhouse:plain")["tier"] == "everything_else"
    assert "Software Engineer Intern" in r.text


def test_dismiss_and_undo_from_board(client, seeded_db):
    r = client.post("/board/dismiss", data={"posting_id": "greenhouse:1"})
    assert r.status_code == 200
    row = _posting(seeded_db, "greenhouse:1")
    assert row["verdict"] == "no_match" and row["verdict_reason"] == review.DISMISS_REASON
    assert "/board/undo-dismiss" in r.text  # undo toast

    r = client.post("/board/undo-dismiss", data={"posting_id": "greenhouse:1"})
    assert _posting(seeded_db, "greenhouse:1")["verdict"] is None


def test_dismiss_unknown_posting_is_404(client):
    assert client.post("/board/dismiss", data={"posting_id": "nope:1"}).status_code == 404


def test_pin_is_sticky_through_rescore(client, seeded_db):
    client.get("/board")  # populate tiers
    r = client.post("/board/pin", data={"posting_id": "greenhouse:plain", "tier": "apply_first"})
    assert r.status_code == 200
    row = _posting(seeded_db, "greenhouse:plain")
    assert row["pinned_tier"] == "apply_first"
    assert row["tier_before_pin"] == "long_shots"  # the promotion is a training label

    # a later rescore/retier must not move the pinned card
    c = db.connect(seeded_db)
    review.refresh_ranking(c)
    inbox = {x["posting_id"]: x for x in review.list_inbox(c)}
    c.close()
    assert inbox["greenhouse:plain"]["tier"] == "apply_first"
    # and the card renders under the pinned section with the pin marker
    r = client.get("/board")
    assert "📌" in r.text


def test_pin_rejects_bad_tier(client):
    r = client.post("/board/pin", data={"posting_id": "greenhouse:1", "tier": "mega"})
    assert r.status_code == 400


def test_unpin_returns_card_to_computed_tier(client, seeded_db):
    client.get("/board")
    client.post("/board/pin", data={"posting_id": "greenhouse:plain", "tier": "apply_first"})
    client.post("/board/unpin", data={"posting_id": "greenhouse:plain"})
    row = _posting(seeded_db, "greenhouse:plain")
    assert row["pinned_tier"] is None and row["tier"] == "long_shots"


def test_drag_pipeline_card_back_to_tier_reenters_inbox(client, seeded_db):
    client.post("/board/move", data={"posting_id": "greenhouse:0", "status": "Applied"})
    r = client.post("/board/pin", data={"posting_id": "greenhouse:0", "tier": "target"})
    assert r.status_code == 200
    c = sqlite3.connect(seeded_db)
    status = c.execute("SELECT status FROM applications WHERE posting_id='greenhouse:0'").fetchone()[0]
    c.close()
    assert status == "Untracked"
    assert _posting(seeded_db, "greenhouse:0")["pinned_tier"] == "target"


def test_table_lens_shows_tier_column_and_dismiss(client):
    r = client.get("/board?view=table")
    assert r.status_code == 200
    assert "<th>Tier</th>" in r.text
    assert "/board/dismiss" in r.text


def test_dismissed_view_lists_and_restores(client, seeded_db):
    client.post("/board/dismiss", data={"posting_id": "greenhouse:1"})
    r = client.get("/board?show=dismissed")
    assert r.status_code == 200
    assert "Software Engineer Intern" in r.text
    assert "/board/undo-dismiss" in r.text
    # restoring from the view brings it back
    client.post("/board/undo-dismiss", data={"posting_id": "greenhouse:1"})
    r = client.get("/board?show=dismissed")
    assert "Nothing dismissed" in r.text


def test_nav_badge_counts_inbox(client):
    r = client.get("/board")
    assert 'id="nav-badge-board"' in r.text
    assert ">4<" in r.text  # all four seeded postings are inbox


def test_hygiene_dismiss_closed_is_undoable(client, seeded_db):
    _exec(seeded_db, "UPDATE postings SET is_active = 0 WHERE posting_id = 'greenhouse:2'")
    r = client.get("/board")
    assert "Dismiss 1 closed" in r.text
    r = client.post("/board/hygiene-closed")
    assert r.status_code == 200
    row = _posting(seeded_db, "greenhouse:2")
    assert row["verdict"] == "no_match" and row["verdict_reason"] == review.CLOSED_REASON
    assert "/board/hygiene-undo" in r.text
    r = client.post("/board/hygiene-undo",
                    data={"reviewed_at": row["reviewed_at"], "reason": review.CLOSED_REASON})
    assert _posting(seeded_db, "greenhouse:2")["verdict"] is None


def test_drawer_offers_pin_and_dismiss(client):
    r = client.get("/board/card/greenhouse:1")
    assert r.status_code == 200
    assert "/board/pin" in r.text
    assert "/board/dismiss" in r.text
    assert "Move back to review" not in r.text


def test_hide_lane_collapses_to_strip_and_persists(client):
    r = client.post("/board/lane", data={"lane": "Interviewing"})
    assert r.status_code == 200
    assert "board-col--strip" in r.text
    assert 'data-status="Interviewing"' not in r.text  # no drop target while hidden
    assert 'data-status="Applied"' in r.text

    # display state survives a fresh page load
    r = client.get("/board")
    assert "board-col--strip" in r.text
    assert 'data-status="Interviewing"' not in r.text

    # toggling again brings the full column back
    r = client.post("/board/lane", data={"lane": "Interviewing"})
    assert "board-col--strip" not in r.text
    assert 'data-status="Interviewing"' in r.text


def test_hide_lane_rejects_unknown_lane(client):
    assert client.post("/board/lane", data={"lane": "Inbox"}).status_code == 400
    assert client.post("/board/lane", data={"lane": "Backlog"}).status_code == 400


def test_hidden_lane_cards_still_in_table_lens(client):
    client.post("/board/move", data={"posting_id": "greenhouse:0", "status": "Applied"})
    client.post("/board/lane", data={"lane": "Applied"})
    r = client.get("/board?view=table")
    assert "greenhouse:0" in r.text  # the applied card still appears in the table
    r = client.get("/board")
    assert 'data-status="Applied"' not in r.text  # strip on the board itself


def test_garbage_hidden_lanes_meta_is_ignored(client, seeded_db):
    c = db.connect(seeded_db)
    db.set_meta(c, "board_hidden_lanes", "not json")
    c.close()
    r = client.get("/board")
    assert r.status_code == 200
    assert "board-col--strip" not in r.text


def test_actions_rescore_inbox(client, seeded_db):
    # With enough seed labels, a dismissal triggers a retrain that scores inbox rows.
    c = db.connect(seeded_db)
    from internshelper.models import Posting

    for i in range(20):
        store.upsert(c, Posting(posting_id=f"m:{i}", source_key="greenhouse:stripe",
                                title="Quant Intern", company="Stripe", url=f"https://x/m{i}",
                                is_cs_relevant=True), now="2026-06-18T09:00:00+00:00")
        review.set_verdict(c, f"m:{i}", "match", "yes", now=f"2026-06-18T09:{i:02d}:00+00:00")
    for i in range(15):
        store.upsert(c, Posting(posting_id=f"n:{i}", source_key="greenhouse:stripe",
                                title="Sales Manager", company="C", url=f"https://x/n{i}"),
                     now="2026-06-18T09:00:00+00:00")
        review.set_verdict(c, f"n:{i}", "no_match", "no", now=f"2026-06-18T10:{i:02d}:00+00:00")
    c.close()
    client.post("/board/dismiss", data={"posting_id": "greenhouse:plain"})
    assert _posting(seeded_db, "greenhouse:1")["rank_score"] is not None
