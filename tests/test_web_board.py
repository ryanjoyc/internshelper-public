"""Board interactions: drag moves, the card drawer, dismiss-from-drawer.

Seeded population: all four seeded postings are inbox rows (no application rows).
"""

import sqlite3

from internshelper import clock, companygroups, db, store, tiers
from internshelper.web.routes.board import _group_by_company


def test_group_by_company_preserves_best_fit_order_and_merges_suffix():
    # Rows arrive best-fit-first; groups should keep that company order, and
    # "Stripe Inc" must merge with "Stripe" (canonical company identity).
    rows = [
        {"company": "Jane Street", "posting_id": "a", "posted_at": "", "first_seen": "1"},
        {"company": "Stripe", "posting_id": "b", "posted_at": "", "first_seen": "1"},
        {"company": "Jane Street", "posting_id": "c", "posted_at": "", "first_seen": "1"},
        {"company": "Stripe Inc", "posting_id": "d", "posted_at": "", "first_seen": "1"},
    ]
    mapping = tiers.company_tier_map([
        companygroups.CompanyGroupEntry("Stripe", "top_target", aliases=["Stripe Inc"])
    ])
    groups = _group_by_company(rows, mapping)
    assert [g["company"] for g in groups] == ["Jane Street", "Stripe"]
    assert [len(g["rows"]) for g in groups] == [2, 2]
    assert [r["posting_id"] for r in groups[1]["rows"]] == ["b", "d"]


def test_group_by_company_blank_company_falls_back_to_unknown():
    groups = _group_by_company([
        {"company": "", "posting_id": "x", "posted_at": "", "first_seen": "1"},
        {"company": None, "posting_id": "y", "posted_at": "", "first_seen": "1"},
    ], {})
    assert len(groups) == 1
    assert groups[0]["company"] == "Unknown"
    assert len(groups[0]["rows"]) == 2


def _app_row(db_path, pid):
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    row = c.execute("SELECT * FROM applications WHERE posting_id = ?", (pid,)).fetchone()
    c.close()
    return row


def _posting_row(db_path, pid):
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    row = c.execute("SELECT * FROM postings WHERE posting_id = ?", (pid,)).fetchone()
    c.close()
    return row


def test_drawer_renders_posting_and_form(client):
    r = client.get("/board/card/greenhouse:0")
    assert r.status_code == 200
    assert "Software Engineer Intern" in r.text
    assert 'name="status"' in r.text
    assert 'type="date"' in r.text
    assert 'name="notes"' in r.text


def test_drawer_unknown_posting_is_404(client):
    assert client.get("/board/card/nope:1").status_code == 404


def test_card_save_writes_application_row(client, seeded_db):
    r = client.post("/board/card/greenhouse:0",
                    data={"status": "Applied", "applied_date": "2026-07-01",
                          "notes": "referral from J", "view": "board"})
    assert r.status_code == 200
    row = _app_row(seeded_db, "greenhouse:0")
    assert (row["status"], row["applied_date"], row["notes"]) == \
        ("Applied", "2026-07-01", "referral from J")


def test_card_save_rejects_unknown_status(client, seeded_db):
    r = client.post("/board/card/greenhouse:0",
                    data={"status": "Ghosted", "applied_date": "", "notes": "", "view": "board"})
    assert r.status_code == 400
    assert _app_row(seeded_db, "greenhouse:0") is None


def test_move_sets_status_and_preserves_notes(client, seeded_db):
    c = db.connect(seeded_db)
    store.set_application(c, "greenhouse:0", status="Interested", notes="keep me",
                          applied_date="2026-06-20")
    c.close()
    r = client.post("/board/move",
                    data={"posting_id": "greenhouse:0", "status": "Interviewing"})
    assert r.status_code == 200
    row = _app_row(seeded_db, "greenhouse:0")
    assert row["status"] == "Interviewing"
    assert row["notes"] == "keep me"
    assert row["applied_date"] == "2026-06-20"
    # The undo toast offers the previous status back.
    assert 'value="Interested"' in r.text


def test_move_to_applied_autofills_todays_date(client, seeded_db):
    r = client.post("/board/move",
                    data={"posting_id": "greenhouse:0", "status": "Applied"})
    assert r.status_code == 200
    row = _app_row(seeded_db, "greenhouse:0")
    assert row["applied_date"] == clock.now_iso()[:10]


def test_move_rejects_unknown_status(client, seeded_db):
    r = client.post("/board/move",
                    data={"posting_id": "greenhouse:0", "status": "Ghosted"})
    assert r.status_code == 400
    assert _app_row(seeded_db, "greenhouse:0") is None


def test_dismiss_hides_posting_and_undo_restores(client, seeded_db):
    r = client.post("/board/dismiss", data={"posting_id": "greenhouse:0"})
    assert r.status_code == 200
    row = _posting_row(seeded_db, "greenhouse:0")
    assert row["verdict"] == "no_match"
    assert "/board/undo-dismiss" in r.text  # the toast offers undo

    r = client.post("/board/undo-dismiss", data={"posting_id": "greenhouse:0"})
    assert r.status_code == 200
    assert _posting_row(seeded_db, "greenhouse:0")["verdict"] is None
