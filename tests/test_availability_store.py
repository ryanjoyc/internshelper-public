"""Persistence and Board-projection tests for availability state."""

from __future__ import annotations

from internshelper import db, ranking, store
from internshelper.availability import SourceAuthority, UserState
from internshelper.availability_checks import (
    CheckStage,
    InvestigationFinding,
    InvestigationObservation,
    NetworkFailure,
    NetworkObservation,
    evaluate_availability,
)
from internshelper.availability_store import (
    confirm_replacement,
    ensure_pending,
    evidence_history,
    get_projection,
    get_state,
    load_current_records,
    persist_evaluation,
    undo_replacement_confirmation,
)
from internshelper.models import Posting


NOW = "2026-08-30T12:00:00+00:00"
ORIGINAL_URL = "https://jobs.example.test/roles/original"
CANDIDATE_URL = "https://jobs.example.test/roles/replacement"
SECOND_CANDIDATE_URL = "https://jobs.example.test/roles/replacement-v2"


def _posting() -> Posting:
    return Posting(
        posting_id="availability:test-role",
        source_key="greenhouse:example",
        title="Software Engineering Intern",
        company="Example Co",
        location="New York, NY",
        url=ORIGINAL_URL,
        is_internship=True,
        is_cs_relevant=True,
    )


def _conn(tmp_path):
    conn = db.connect(tmp_path / "availability.db")
    db.init_db(conn)
    store.upsert(conn, _posting(), now=NOW)
    return conn


def _network(
    *,
    sequence: int,
    attempt: int,
    status: int | None = None,
    failure: NetworkFailure | None = None,
    body: str = "",
):
    return NetworkObservation(
        sequence=sequence,
        observed_at=f"2026-08-30T12:{sequence:02d}:00+00:00",
        stage=(
            CheckStage.INITIAL_VALIDATION
            if attempt == 1
            else CheckStage.SCHEDULED_RETRY
        ),
        target="original_url",
        attempt=attempt,
        status=status,
        failure=failure,
        body=body,
        employer_hosted=True,
    )


def _evaluation(*observations, stages=()):
    return evaluate_availability(
        source_authority=SourceAuthority.FIRST_PARTY_ATS,
        user_state=UserState.ORDINARY,
        observations=tuple(observations),
        investigation_stages=tuple(stages),
    )


def test_schema_creates_availability_tables_idempotently(tmp_path):
    conn = db.connect(tmp_path / "schema.db")

    db.init_db(conn)
    db.init_db(conn)

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {"availability_state", "availability_evidence"} <= tables


def test_pending_community_posting_is_hidden_until_validation_finishes(tmp_path):
    conn = _conn(tmp_path)
    ensure_pending(
        conn,
        _posting().posting_id,
        SourceAuthority.COMMUNITY_LIST,
        now=NOW,
    )

    assert store.inbox_with_status(conn) == []

    evaluation = evaluate_availability(
        source_authority=SourceAuthority.COMMUNITY_LIST,
        user_state=UserState.ORDINARY,
        observations=(
            _network(sequence=1, attempt=1, failure=NetworkFailure.TIMEOUT),
        ),
    )
    persist_evaluation(conn, _posting().posting_id, evaluation, updated_at=NOW)

    rows = store.inbox_with_status(conn)
    assert len(rows) == 1
    assert rows[0]["availability"] == "uncertain"
    assert rows[0]["primary_action"] == "verify"


def test_persisted_evidence_keeps_order_and_never_changes_ranking_or_curation(tmp_path):
    conn = _conn(tmp_path)
    conn.execute(
        "UPDATE postings SET rank_score = 81.5, rank_reasons = ?, verdict = ? "
        "WHERE posting_id = ?",
        ('["existing"]', "match", _posting().posting_id),
    )
    conn.commit()
    labels_before = ranking.gather_labels(conn)
    evaluation = _evaluation(
        _network(sequence=1, attempt=1, status=404),
        _network(sequence=2, attempt=2, status=404),
    )

    persist_evaluation(conn, _posting().posting_id, evaluation, updated_at=NOW)

    history = evidence_history(conn, _posting().posting_id)
    row = conn.execute(
        "SELECT url, verdict, rank_score, rank_reasons FROM postings WHERE posting_id = ?",
        (_posting().posting_id,),
    ).fetchone()
    assert [item["attempt"] for item in history] == [1, 2]
    assert [item["signal"] for item in history] == ["http_404", "http_404"]
    assert dict(row) == {
        "url": ORIGINAL_URL,
        "verdict": "match",
        "rank_score": 81.5,
        "rank_reasons": '["existing"]',
    }
    assert ranking.gather_labels(conn) == labels_before


def test_scheduled_retry_combines_with_the_current_evidence_window(tmp_path):
    conn = _conn(tmp_path)
    first = _evaluation(_network(sequence=1, attempt=1, status=404))
    persist_evaluation(conn, _posting().posting_id, first, updated_at=NOW)

    second = evaluate_availability(
        source_authority=SourceAuthority.FIRST_PARTY_ATS,
        user_state=UserState.ORDINARY,
        observations=(_network(sequence=2, attempt=2, status=404),),
        previous_records=load_current_records(conn, _posting().posting_id),
    )
    persist_evaluation(conn, _posting().posting_id, second, updated_at=NOW)

    assert second.decision.availability.value == "closed"
    assert [row["attempt"] for row in evidence_history(conn, _posting().posting_id)] == [1, 2]


def test_closed_ordinary_is_archived_but_interested_and_applied_remain_visible(tmp_path):
    conn = _conn(tmp_path)
    evaluation = _evaluation(
        _network(sequence=1, attempt=1, status=410),
        _network(sequence=2, attempt=2, status=410),
    )
    persist_evaluation(conn, _posting().posting_id, evaluation, updated_at=NOW)

    assert store.inbox_with_status(conn) == []
    assert store.dismissed_count(conn) == 0

    store.set_application_status(conn, _posting().posting_id, "Interested")
    interested = store.inbox_with_status(conn)
    assert interested[0]["board_treatment"] == "visible_closed"

    store.set_application_status(conn, _posting().posting_id, "Applied")
    applied = store.inbox_with_status(conn)
    assert applied[0]["board_treatment"] == "visible_closed"


def test_confirmation_uses_pending_candidate_and_survives_later_upsert(tmp_path):
    conn = _conn(tmp_path)
    evaluation = _evaluation(
        _network(sequence=1, attempt=1, status=404),
        InvestigationObservation(
            sequence=2,
            observed_at="2026-08-30T12:02:00+00:00",
            stage=CheckStage.USER_INVESTIGATION,
            target="replacement_candidate",
            attempt=1,
            finding=InvestigationFinding.REPLACEMENT_FOUND,
            candidate_url=CANDIDATE_URL,
        ),
        stages=("queued", "review_finding", "await_user_confirmation"),
    )
    persist_evaluation(conn, _posting().posting_id, evaluation, updated_at=NOW)
    before = get_projection(conn, _posting().posting_id)

    confirmed = confirm_replacement(conn, _posting().posting_id, confirmed_at=NOW)
    store.upsert(conn, _posting(), now="2026-08-30T13:00:00+00:00")
    after = get_projection(conn, _posting().posting_id)
    state = get_state(conn, _posting().posting_id)

    assert before.decision.primary_action.value == "confirm_replacement"
    assert before.effective_url == ORIGINAL_URL
    assert confirmed == CANDIDATE_URL
    assert after.effective_url == CANDIDATE_URL
    assert after.decision.primary_action.value == "verify"
    assert state["pending_candidate_url"] is None
    assert state["confirmed_url"] == CANDIDATE_URL
    assert conn.execute(
        "SELECT url FROM postings WHERE posting_id = ?", (_posting().posting_id,)
    ).fetchone()[0] == ORIGINAL_URL

    assert undo_replacement_confirmation(
        conn, _posting().posting_id, updated_at="2026-08-30T14:00:00+00:00"
    )
    restored = get_projection(conn, _posting().posting_id)
    restored_state = get_state(conn, _posting().posting_id)
    assert restored.effective_url == ORIGINAL_URL
    assert restored.decision.primary_action.value == "confirm_replacement"
    assert restored_state["confirmed_url"] is None
    assert restored_state["pending_candidate_url"] == CANDIDATE_URL


def test_unconfirmed_candidate_survives_a_later_uncertain_check(tmp_path):
    conn = _conn(tmp_path)
    replacement = _evaluation(
        _network(sequence=1, attempt=1, status=404),
        InvestigationObservation(
            sequence=2,
            observed_at="2026-08-30T12:02:00+00:00",
            stage=CheckStage.USER_INVESTIGATION,
            target="replacement_candidate",
            attempt=1,
            finding=InvestigationFinding.REPLACEMENT_FOUND,
            candidate_url=CANDIDATE_URL,
        ),
        stages=("queued", "review_finding", "await_user_confirmation"),
    )
    persist_evaluation(conn, _posting().posting_id, replacement, updated_at=NOW)
    outage = _evaluation(
        _network(sequence=3, attempt=2, failure=NetworkFailure.TIMEOUT)
    )

    persist_evaluation(
        conn,
        _posting().posting_id,
        outage,
        updated_at="2026-08-30T13:00:00+00:00",
    )

    state = get_state(conn, _posting().posting_id)
    projection = get_projection(conn, _posting().posting_id)
    assert state["pending_candidate_url"] == CANDIDATE_URL
    assert projection.decision.primary_action.value == "confirm_replacement"
    assert projection.decision.replacement_candidate_url == CANDIDATE_URL


def test_undo_confirmation_preserves_a_newer_pending_candidate(tmp_path):
    conn = _conn(tmp_path)
    first = _evaluation(
        InvestigationObservation(
            sequence=1,
            observed_at=NOW,
            stage=CheckStage.USER_INVESTIGATION,
            target="replacement_candidate",
            attempt=1,
            finding=InvestigationFinding.REPLACEMENT_FOUND,
            candidate_url=CANDIDATE_URL,
        ),
        stages=("queued", "review_finding", "await_user_confirmation"),
    )
    persist_evaluation(conn, _posting().posting_id, first, updated_at=NOW)
    confirm_replacement(conn, _posting().posting_id, confirmed_at=NOW)
    second = _evaluation(
        InvestigationObservation(
            sequence=2,
            observed_at="2026-08-30T13:00:00+00:00",
            stage=CheckStage.USER_INVESTIGATION,
            target="replacement_candidate",
            attempt=1,
            finding=InvestigationFinding.REPLACEMENT_FOUND,
            candidate_url=SECOND_CANDIDATE_URL,
        ),
        stages=("queued", "review_finding", "await_user_confirmation"),
    )
    persist_evaluation(
        conn,
        _posting().posting_id,
        second,
        updated_at="2026-08-30T13:00:00+00:00",
    )

    assert undo_replacement_confirmation(
        conn, _posting().posting_id, updated_at="2026-08-30T14:00:00+00:00"
    )

    state = get_state(conn, _posting().posting_id)
    assert state["confirmed_url"] is None
    assert state["pending_candidate_url"] == SECOND_CANDIDATE_URL


def test_confirmed_candidate_stays_visible_and_uncertain_until_it_is_checked(tmp_path):
    conn = _conn(tmp_path)
    evaluation = _evaluation(
        _network(sequence=1, attempt=1, status=404),
        _network(sequence=2, attempt=2, status=404),
        InvestigationObservation(
            sequence=3,
            observed_at="2026-08-30T12:03:00+00:00",
            stage=CheckStage.USER_INVESTIGATION,
            target="replacement_candidate",
            attempt=2,
            finding=InvestigationFinding.REPLACEMENT_FOUND,
            candidate_url=CANDIDATE_URL,
        ),
        stages=("queued", "review_finding", "await_user_confirmation"),
    )
    persist_evaluation(conn, _posting().posting_id, evaluation, updated_at=NOW)

    confirm_replacement(conn, _posting().posting_id, confirmed_at=NOW)

    projection = get_projection(conn, _posting().posting_id)
    assert projection.visible is True
    assert projection.effective_url == CANDIDATE_URL
    assert projection.decision.availability.value == "uncertain"
    assert projection.decision.primary_action.value == "verify"
