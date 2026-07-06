"""Board interactions: drag moves, the card drawer, move-back-to-review.

Seeded board population: greenhouse:0 is the only confirmed match (no application row).
"""

import sqlite3

from internshelper import clock, db, store


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


def test_unmatch_returns_posting_to_review(client, seeded_db):
    r = client.post("/board/unmatch",
                    data={"posting_id": "greenhouse:0", "view": "board"})
    assert r.status_code == 200
    row = _posting_row(seeded_db, "greenhouse:0")
    assert row["review_status"] == "pending"
    assert row["verdict"] is None
