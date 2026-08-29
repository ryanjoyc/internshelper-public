"""The Board: company sections, application moves, table lens, dismissed view.

Seeded population (conftest): four inbox postings — greenhouse:0 (old match verdict),
greenhouse:1 + greenhouse:2 (candidates), greenhouse:plain (non-candidate, company
Bistro). No application rows, no rank scores (cold start).
"""

import sqlite3

from internshelper import db, review, store
from internshelper.models import Posting


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


def test_board_groups_inbox_by_company(client):
    r = client.get("/board")
    assert r.status_code == 200
    # Fixed company-priority sections contain one collapsed card per company.
    assert "co-group" in r.text
    assert ">Stripe<" in r.text and ">Bistro<" in r.text  # company group headers
    assert "tierOpen('co:stripe', false)" in r.text
    assert "tierOpen('co:bistro', false)" in r.text
    assert "Top targets" in r.text and "Known companies" in r.text
    assert "Worth discovering" in r.text and "Unclassified" in r.text
    assert "data-tier=" not in r.text
    assert "Long shots" not in r.text
    # a raw double quote here would terminate the double-quoted x-data attribute
    # early and kill Alpine for the whole section (the "inbox stuck closed" bug)
    assert 'tierOpen("' not in r.text and 'tierSave("' not in r.text
    assert "Software Engineer Intern" in r.text  # cards still present (inside collapsed groups)
    assert "Line Cook" in r.text


def test_company_group_ignores_rank_and_candidate_flags(client, seeded_db):
    client.get("/board")  # triggers the self-heal retier
    assert _posting(seeded_db, "greenhouse:1")["tier"] == "top_target"
    assert _posting(seeded_db, "greenhouse:plain")["tier"] == "unclassified"


def test_configured_company_lands_in_top_targets(client, seeded_db):
    _exec(seeded_db, "UPDATE postings SET rank_score = 0.8, tier = NULL")
    r = client.get("/board")
    assert _posting(seeded_db, "greenhouse:1")["tier"] == "top_target"
    assert _posting(seeded_db, "greenhouse:plain")["tier"] == "unclassified"
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


def test_company_group_control_moves_all_company_postings(client, seeded_db, company_groups_file):
    r = client.post("/board/company-group", data={"company": "Bistro", "group": "known"})
    assert r.status_code == 200
    assert _posting(seeded_db, "greenhouse:plain")["tier"] == "known"
    assert "Bistro moved to Known companies" in r.text
    assert "name: Bistro" in company_groups_file.read_text()


def test_company_group_control_rejects_bad_group(client):
    r = client.post("/board/company-group", data={"company": "Stripe", "group": "mega"})
    assert r.status_code == 400
    r = client.post(
        "/board/company-group", data={"company": "Bistro", "group": "discovery"}
    )
    assert r.status_code == 400 and "proposal" in r.text


def test_drag_pipeline_card_back_to_company_reenters_inbox(client, seeded_db):
    client.post("/board/move", data={"posting_id": "greenhouse:0", "status": "Applied"})
    r = client.post("/board/move", data={"posting_id": "greenhouse:0", "status": "Untracked"})
    assert r.status_code == 200
    c = sqlite3.connect(seeded_db)
    status = c.execute("SELECT status FROM applications WHERE posting_id='greenhouse:0'").fetchone()[0]
    c.close()
    assert status == "Untracked"


def test_table_lens_has_group_column_and_dismiss(client):
    r = client.get("/board?view=table")
    assert r.status_code == 200
    assert "<th>Group</th>" in r.text
    assert "<th>Company</th>" in r.text
    assert "/board/dismiss" in r.text
    assert 'data-row-open' in r.text
    assert '<tr class="table-row-click" data-drawer-trigger' not in r.text


def test_board_table_uses_stable_hundred_row_pages(client, seeded_db):
    conn = db.connect(seeded_db)
    for i in range(205):
        store.upsert(
            conn,
            Posting(
                posting_id=f"page:{i:03d}",
                source_key="greenhouse:paging",
                title=f"Paged role {i:03d}",
                company="Paging Co",
                url=f"https://example.test/{i}",
            ),
            now=f"2026-08-25T12:{i % 60:02d}:00+00:00",
        )
    conn.close()

    first = client.get("/board?view=table")
    second = client.get("/board?view=table&page=2")
    last = client.get("/board?view=table&page=999")
    assert first.text.count("data-row-open") == 100
    assert second.text.count("data-row-open") == 100
    assert last.text.count("data-row-open") == 9
    assert "Page 3 of 3" in last.text


def test_dismissed_lens_paginates_and_restore_stays_in_lens(client, seeded_db):
    conn = db.connect(seeded_db)
    for i in range(105):
        store.upsert(
            conn,
            Posting(
                posting_id=f"dismissed:{i:03d}",
                source_key="greenhouse:dismissed",
                title=f"Dismissed role {i:03d}",
                company="Archive Co",
                url=f"https://example.test/d/{i}",
            ),
            now="2026-08-25T12:00:00+00:00",
        )
    conn.execute(
        "UPDATE postings SET verdict='no_match', reviewed_at='2026-08-26T12:00:00+00:00' "
        "WHERE posting_id LIKE 'dismissed:%'"
    )
    conn.commit()
    conn.close()

    r = client.get("/board?show=dismissed&page=2")
    assert "Page 2 of 2" in r.text
    assert r.text.count("/board/undo-dismiss") == 5
    posting_id = "dismissed:000"
    r = client.post("/board/undo-dismiss", data={
        "posting_id": posting_id, "return_view": "dismissed", "page": 2,
    })
    assert r.status_code == 200
    assert "Dismissed postings pagination" in r.text
    assert "Company inbox" not in r.text


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


def test_drawer_offers_dismiss_but_no_tier_pin(client):
    r = client.get("/board/card/greenhouse:1")
    assert r.status_code == 200
    assert "/board/dismiss" in r.text
    assert "/board/pin" not in r.text  # tier pinning sidelined with the tiers
    assert "Move back to review" not in r.text


def test_flag_from_board_hides_card_with_undo_toast(client, seeded_db):
    r = client.post("/board/flag", data={"posting_id": "greenhouse:1"})
    assert r.status_code == 200
    row = _posting(seeded_db, "greenhouse:1")
    assert row["flagged_at"] is not None
    assert row["flag_reason"] == review.FLAG_REASON_DEFAULT
    assert row["verdict"] is None  # not a dismissal, no training label
    assert "/board/unflag" in r.text  # undo toast
    assert "Flagged for review" in r.text
    # the badge count drops (4 seeded -> 3 inbox)
    assert ">3<" in r.text

    r = client.post("/board/unflag", data={"posting_id": "greenhouse:1"})
    assert r.status_code == 200
    assert _posting(seeded_db, "greenhouse:1")["flagged_at"] is None


def test_flag_with_reason_and_unknown_ids(client, seeded_db):
    r = client.post("/board/flag",
                    data={"posting_id": "greenhouse:2", "reason": "careers page 404s"})
    assert r.status_code == 200
    assert _posting(seeded_db, "greenhouse:2")["flag_reason"] == "careers page 404s"
    assert client.post("/board/flag", data={"posting_id": "nope:1"}).status_code == 404
    assert client.post("/board/unflag", data={"posting_id": "nope:1"}).status_code == 404


def test_flagged_view_lists_and_restores(client, seeded_db):
    client.post("/board/flag",
                data={"posting_id": "greenhouse:1", "reason": "link shows nothing"})
    r = client.get("/board?show=flagged")
    assert r.status_code == 200
    assert "Software Engineer Intern" in r.text
    assert "link shows nothing" in r.text
    assert "Flagged (1)" in r.text  # seg link carries the count
    assert "/board/unflag" in r.text
    client.post("/board/unflag", data={"posting_id": "greenhouse:1"})
    r = client.get("/board?show=flagged")
    assert "Nothing flagged" in r.text
    assert "Flagged (" not in r.text  # count hidden at zero


def test_cards_and_drawer_offer_flag(client):
    r = client.get("/board")
    assert "card-flag" in r.text
    r = client.get("/board/card/greenhouse:1")
    assert "/board/flag" in r.text
    assert "Flag for review" in r.text


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
