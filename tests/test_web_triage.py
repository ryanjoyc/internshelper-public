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


def test_queue_partial_respects_offset(client):
    r = client.get("/review/queue", params={"offset": 2})
    assert r.status_code == 200
    assert r.text.count('id="row-') == 1  # 3 pending, offset 2 -> the last one
    assert "greenhouse:plain" in r.text


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
