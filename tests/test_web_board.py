"""Board interactions: drag moves, the card drawer, dismiss-from-drawer.

Seeded population: all four seeded postings are inbox rows (no application rows).
"""

import html
import sqlite3

import pytest

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


def _dup_count(db_path, survivor):
    c = sqlite3.connect(db_path)
    n = c.execute("SELECT count(*) FROM postings WHERE duplicate_of = ?", (survivor,)).fetchone()[0]
    c.close()
    return n


def test_duplicates_lens_merge_and_unmerge(client, seeded_db):
    # greenhouse:0/1/2 are all "Stripe / Software Engineer Intern" -> one fuzzy group.
    r = client.get("/board?show=duplicates")
    assert r.status_code == 200
    assert "Possible duplicates" in r.text and "Software Engineer Intern" in r.text

    # Merge the group into survivor greenhouse:0.
    r = client.post("/board/dedup/confirm", data={
        "survivor_id": "greenhouse:0",
        "posting_ids": "greenhouse:0,greenhouse:1,greenhouse:2",
    })
    assert r.status_code == 200
    assert _dup_count(seeded_db, "greenhouse:0") == 2  # two losers hidden, survivor kept
    assert "/board/dedup/undo" in r.text
    assert 'name="posting_ids" value="greenhouse:1,greenhouse:2"' in r.text

    # The losers are gone from the Inbox query (INBOX_SQL excludes duplicate_of).
    from internshelper import db as _db, store
    c = _db.connect(seeded_db)
    inbox_ids = {row["posting_id"] for row in store.inbox_with_status(c)}
    assert "greenhouse:1" not in inbox_ids and "greenhouse:2" not in inbox_ids
    assert "greenhouse:0" in inbox_ids
    c.close()

    # Un-merge one -> it returns.
    r = client.post("/board/dedup/undo", data={"posting_id": "greenhouse:1"})
    assert r.status_code == 200
    assert _dup_count(seeded_db, "greenhouse:0") == 1


def test_duplicate_merge_toast_undo_restores_the_whole_batch(client, seeded_db):
    client.post("/board/dedup/confirm", data={
        "survivor_id": "greenhouse:0",
        "posting_ids": "greenhouse:0,greenhouse:1,greenhouse:2",
    })
    r = client.post("/board/dedup/undo", data={
        "posting_ids": "greenhouse:1,greenhouse:2",
    })
    assert r.status_code == 200
    assert _dup_count(seeded_db, "greenhouse:0") == 0

    # Repeating Undo is harmless and never hides/restores unrelated rows.
    assert client.post("/board/dedup/undo", data={
        "posting_ids": "greenhouse:1,greenhouse:2",
    }).status_code == 200
    assert _dup_count(seeded_db, "greenhouse:0") == 0


def test_duplicate_merge_rejects_invalid_or_mixed_groups(client, seeded_db):
    for data in (
        {
            "survivor_id": "missing:survivor",
            "posting_ids": "greenhouse:0,greenhouse:1",
        },
        {
            "survivor_id": "greenhouse:0",
            "posting_ids": "greenhouse:0,greenhouse:plain",
        },
    ):
        response = client.post("/board/dedup/confirm", data=data)
        assert response.status_code == 400

    assert client.post("/board/dedup/keep", data={
        "posting_ids": "greenhouse:0,greenhouse:plain",
    }).status_code == 400

    assert _dup_count(seeded_db, "missing:survivor") == 0
    assert _posting_row(seeded_db, "greenhouse:plain")["duplicate_of"] is None


def test_stale_duplicate_undo_does_not_clear_a_newer_merge(client, seeded_db):
    client.post("/board/dedup/confirm", data={
        "survivor_id": "greenhouse:0",
        "posting_ids": "greenhouse:0,greenhouse:1,greenhouse:2",
    })
    c = db.connect(seeded_db)
    c.execute(
        "UPDATE postings SET duplicate_of = ? WHERE posting_id = ?",
        ("greenhouse:2", "greenhouse:1"),
    )
    c.commit()
    c.close()

    response = client.post("/board/dedup/undo", data={
        "posting_ids": "greenhouse:1",
        "survivor_id": "greenhouse:0",
    })
    assert response.status_code == 200
    assert _posting_row(seeded_db, "greenhouse:1")["duplicate_of"] == "greenhouse:2"


def test_keep_separate_has_complete_undo(client, seeded_db):
    r = client.post("/board/dedup/keep", data={
        "posting_ids": "greenhouse:0,greenhouse:1,greenhouse:2",
    })
    assert r.status_code == 200
    assert "/board/dedup/undo-keep" in r.text
    assert "Possible duplicates" not in r.text
    assert "Reviewed as separate" in r.text

    reloaded = client.get("/board?show=duplicates")
    assert "Reviewed as separate" in reloaded.text
    assert "/board/dedup/undo-keep" in reloaded.text

    r = client.post("/board/dedup/undo-keep", data={
        "posting_ids": "greenhouse:0,greenhouse:1,greenhouse:2",
    })
    assert r.status_code == 200
    assert "Possible duplicates" in r.text


def test_duplicate_lens_blocks_merge_when_multiple_rows_have_applications(
    client, seeded_db
):
    c = db.connect(seeded_db)
    store.set_application(c, "greenhouse:0", status="Applied", notes="first")
    store.set_application(c, "greenhouse:1", status="Interviewing", notes="second")
    c.close()

    response = client.get("/board?show=duplicates")
    assert response.status_code == 200
    assert "Saved application: Applied" in response.text
    assert "Saved application: Interviewing" in response.text
    assert "Merge is unavailable because more than one record has saved application progress" in response.text
    assert "Merge and keep" not in response.text

    response = client.post("/board/dedup/confirm", data={
        "survivor_id": "greenhouse:0",
        "posting_ids": "greenhouse:0,greenhouse:1,greenhouse:2",
    })
    assert response.status_code == 400
    assert _dup_count(seeded_db, "greenhouse:0") == 0


def test_duplicate_undo_requires_at_least_one_id(client):
    assert client.post("/board/dedup/keep", data={"posting_ids": ""}).status_code == 422
    assert client.post("/board/dedup/undo", data={}).status_code == 400
    assert client.post("/board/dedup/undo-keep", data={"posting_ids": ""}).status_code == 400


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


def test_quick_status_control_renders_only_for_pipeline_cards(client, seeded_db):
    inbox = client.get("/board")
    assert inbox.status_code == 200
    assert "data-quick-status" not in inbox.text

    c = db.connect(seeded_db)
    store.set_application(c, "greenhouse:0", status="Interviewing",
                          notes="First line\nSecond <private> line",
                          applied_date="2026-06-20")
    c.close()
    pipeline = client.get("/board")
    assert pipeline.status_code == 200
    assert pipeline.text.count("data-status-trigger") == 1
    assert "Current status: Interviewing" in pipeline.text
    assert 'value="Interviewing"' in pipeline.text
    assert "checked disabled" in pipeline.text
    assert "First line\nSecond &lt;private&gt; line" in pipeline.text
    assert pipeline.text.count('name="status"') == len(store.PIPELINE_STATUSES)


@pytest.mark.parametrize(
    ("current", "target"),
    [(current, target) for current in store.PIPELINE_STATUSES
     for target in store.PIPELINE_STATUSES if target != current],
)
def test_quick_status_every_transition_saves_complete_notes(
    client, seeded_db, current, target
):
    c = db.connect(seeded_db)
    store.set_application(c, "greenhouse:0", status=current, notes="previous notes",
                          applied_date="2026-06-20")
    c.close()

    complete_notes = f"edited for {current} -> {target}\nfinal line"
    r = client.post("/board/move", data={
        "posting_id": "greenhouse:0", "status": target, "notes": complete_notes,
    })
    assert r.status_code == 200
    row = _app_row(seeded_db, "greenhouse:0")
    assert (row["status"], row["notes"], row["applied_date"]) == (
        target, complete_notes, "2026-06-20",
    )
    assert '<textarea name="notes" hidden>previous notes</textarea>' in r.text
    assert (
        f'<textarea name="expected_notes" hidden>{html.escape(complete_notes)}</textarea>'
        in r.text
    )


def test_quick_status_can_clear_notes_and_undo_restores_status_and_notes(client, seeded_db):
    c = db.connect(seeded_db)
    store.set_application(c, "greenhouse:0", status="Applied",
                          notes="exact old notes\nwith a second line",
                          applied_date="2026-06-20")
    c.close()

    r = client.post("/board/move", data={
        "posting_id": "greenhouse:0", "status": "Rejected", "notes": "",
    })
    assert r.status_code == 200
    row = _app_row(seeded_db, "greenhouse:0")
    assert (row["status"], row["notes"], row["applied_date"]) == (
        "Rejected", "", "2026-06-20",
    )
    assert '<textarea name="expected_notes" hidden></textarea>' in r.text

    r = client.post("/board/move", data={
        "posting_id": "greenhouse:0", "status": "Applied", "undo": "1",
        "notes": "exact old notes\nwith a second line",
        "expected_status": "Rejected", "expected_notes": "",
        "expected_applied_date": "2026-06-20",
    })
    assert r.status_code == 200
    row = _app_row(seeded_db, "greenhouse:0")
    assert (row["status"], row["notes"], row["applied_date"]) == (
        "Applied", "exact old notes\nwith a second line", "2026-06-20",
    )


def test_quick_status_undo_restores_null_notes_without_stamping_date(client, seeded_db):
    c = db.connect(seeded_db)
    store.set_application(c, "greenhouse:0", status="Applied", notes=None,
                          applied_date=None)
    c.close()

    r = client.post("/board/move", data={
        "posting_id": "greenhouse:0", "status": "Offer", "notes": "new",
    })
    assert r.status_code == 200
    assert 'name="notes_null" value="1"' in r.text
    r = client.post("/board/move", data={
        "posting_id": "greenhouse:0", "status": "Applied", "undo": "1",
        "notes_null": "1",
        "expected_status": "Offer", "expected_notes": "new",
        "expected_applied_date_null": "1",
    })
    assert r.status_code == 200
    row = _app_row(seeded_db, "greenhouse:0")
    assert (row["status"], row["notes"], row["applied_date"]) == (
        "Applied", None, None,
    )


def test_quick_status_rejects_current_status(client, seeded_db):
    c = db.connect(seeded_db)
    store.set_application(c, "greenhouse:0", status="Offer", notes="unchanged",
                          applied_date="2026-06-20")
    c.close()
    r = client.post("/board/move", data={
        "posting_id": "greenhouse:0", "status": "Offer", "notes": "changed",
    })
    assert r.status_code == 400
    row = _app_row(seeded_db, "greenhouse:0")
    assert (row["status"], row["notes"]) == ("Offer", "unchanged")


def test_move_to_applied_autofills_todays_date(client, seeded_db):
    r = client.post("/board/move",
                    data={"posting_id": "greenhouse:0", "status": "Applied"})
    assert r.status_code == 200
    row = _app_row(seeded_db, "greenhouse:0")
    assert row["applied_date"] == clock.now_iso()[:10]


def test_move_undo_restores_absent_application_row(client, seeded_db):
    response = client.post(
        "/board/move",
        data={"posting_id": "greenhouse:0", "status": "Applied"},
    )
    assert response.status_code == 200
    assert 'name="application_absent" value="1"' in response.text
    assert 'name="expected_status" value="Applied"' in response.text

    response = client.post(
        "/board/move",
        data={
            "posting_id": "greenhouse:0",
            "status": "Untracked",
            "undo": "1",
            "application_absent": "1",
            "expected_status": "Applied",
            "expected_notes_null": "1",
            "expected_applied_date": clock.now_iso()[:10],
        },
    )
    assert response.status_code == 200
    assert _app_row(seeded_db, "greenhouse:0") is None
    assert "applied " not in response.text


def test_stale_move_undo_does_not_delete_newer_application_changes(client, seeded_db):
    client.post(
        "/board/move",
        data={"posting_id": "greenhouse:0", "status": "Applied"},
    )
    conn = db.connect(seeded_db)
    store.set_application(
        conn,
        "greenhouse:0",
        status="Offer",
        notes="newer decision",
        applied_date="2026-08-28",
    )
    conn.close()

    response = client.post(
        "/board/move",
        data={
            "posting_id": "greenhouse:0",
            "status": "Untracked",
            "undo": "1",
            "application_absent": "1",
            "expected_status": "Applied",
            "expected_notes_null": "1",
            "expected_applied_date": clock.now_iso()[:10],
        },
    )
    assert response.status_code == 409
    row = _app_row(seeded_db, "greenhouse:0")
    assert (row["status"], row["notes"], row["applied_date"]) == (
        "Offer", "newer decision", "2026-08-28",
    )


def test_move_rejects_unknown_status(client, seeded_db):
    r = client.post("/board/move",
                    data={"posting_id": "greenhouse:0", "status": "Ghosted"})
    assert r.status_code == 400
    assert _app_row(seeded_db, "greenhouse:0") is None


def test_move_undo_rejects_unknown_restore_status(client, seeded_db):
    c = db.connect(seeded_db)
    store.set_application(
        c, "greenhouse:0", status="Applied", notes="unchanged",
        applied_date="2026-08-28",
    )
    c.close()

    response = client.post("/board/move", data={
        "posting_id": "greenhouse:0",
        "status": "Ghosted",
        "undo": "1",
        "expected_status": "Applied",
        "expected_notes": "unchanged",
        "expected_applied_date": "2026-08-28",
    })
    assert response.status_code == 400
    row = _app_row(seeded_db, "greenhouse:0")
    assert (row["status"], row["notes"], row["applied_date"]) == (
        "Applied", "unchanged", "2026-08-28",
    )


def test_move_rejects_undo_without_expected_state(client, seeded_db):
    c = db.connect(seeded_db)
    store.set_application(c, "greenhouse:0", status="Applied", notes="preserve")
    c.close()

    response = client.post("/board/move", data={
        "posting_id": "greenhouse:0", "status": "Untracked", "undo": "1",
    })
    assert response.status_code == 400
    row = _app_row(seeded_db, "greenhouse:0")
    assert (row["status"], row["notes"]) == ("Applied", "preserve")


def test_move_from_legacy_status_undo_normalizes_to_untracked(client, seeded_db):
    c = db.connect(seeded_db)
    c.execute(
        "INSERT INTO applications (posting_id, status, notes, applied_date) VALUES (?,?,?,?)",
        ("greenhouse:0", "Contacted", "legacy notes", "2026-08-20"),
    )
    c.commit()
    c.close()

    response = client.post("/board/move", data={
        "posting_id": "greenhouse:0", "status": "Applied",
    })
    assert response.status_code == 200
    assert 'name="status" value="Untracked"' in response.text
    assert 'name="notes" hidden>legacy notes</textarea>' in response.text

    response = client.post("/board/move", data={
        "posting_id": "greenhouse:0",
        "status": "Untracked",
        "notes": "legacy notes",
        "applied_date": "2026-08-20",
        "undo": "1",
        "expected_status": "Applied",
        "expected_notes": "legacy notes",
        "expected_applied_date": "2026-08-20",
    })
    assert response.status_code == 200
    row = _app_row(seeded_db, "greenhouse:0")
    assert (row["status"], row["notes"], row["applied_date"]) == (
        "Untracked", "legacy notes", "2026-08-20",
    )


def test_dismiss_hides_posting_and_undo_restores(client, seeded_db):
    r = client.post("/board/dismiss", data={"posting_id": "greenhouse:0"})
    assert r.status_code == 200
    row = _posting_row(seeded_db, "greenhouse:0")
    assert row["verdict"] == "no_match"
    assert "/board/undo-dismiss" in r.text  # the toast offers undo

    r = client.post("/board/undo-dismiss", data={"posting_id": "greenhouse:0"})
    assert r.status_code == 200
    assert _posting_row(seeded_db, "greenhouse:0")["verdict"] is None
