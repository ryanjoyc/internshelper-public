"""Board availability actions, progress, and replacement confirmation."""

from __future__ import annotations

import pytest

from internshelper import db, store
from internshelper.availability import SourceAuthority, UserState
from internshelper.availability_checks import (
    CheckStage,
    InvestigationFinding,
    InvestigationObservation,
    NetworkObservation,
    evaluate_availability,
)
from internshelper.availability_service import verify_posting
from internshelper.availability_store import get_state, persist_evaluation


NOW = "2026-08-30T12:00:00+00:00"


def _network(*, status, checker_only=False, sequence=1, attempt=1):
    return NetworkObservation(
        sequence=sequence,
        observed_at=NOW,
        stage=CheckStage.INITIAL_VALIDATION,
        target="original_url",
        attempt=attempt,
        status=status,
        body="Page not found" if status == 404 else "Automated request denied",
        employer_hosted=True,
        checker_only=checker_only,
    )


def _persist(client, observations, stages=()):
    conn = db.connect(client.app.state.db_path)
    evaluation = evaluate_availability(
        source_authority=SourceAuthority.FIRST_PARTY_ATS,
        user_state=UserState.ORDINARY,
        observations=tuple(observations),
        investigation_stages=tuple(stages),
    )
    persist_evaluation(conn, "greenhouse:1", evaluation, updated_at=NOW)
    conn.close()


def test_single_404_drawer_guards_apply_with_verify_primary(client):
    _persist(client, (_network(status=404),))

    response = client.get("/board/card/greenhouse:1")

    assert response.status_code == 200
    assert "Availability uncertain" in response.text
    assert "Verify and find application" in response.text
    assert "Open original" in response.text
    assert response.text.index("Verify and find application") < response.text.index("Open original")


def test_checker_only_403_keeps_apply_primary(client):
    _persist(client, (_network(status=403, checker_only=True),))

    response = client.get("/board/card/greenhouse:1")

    assert response.status_code == 200
    assert response.text.index(">Apply<") < response.text.index("Verify and find application")


def test_table_view_exposes_uncertain_status_and_verify_as_primary(client):
    _persist(client, (_network(status=404),))

    response = client.get("/board?view=table")

    assert response.status_code == 200
    assert "Availability uncertain" in response.text
    assert 'action="/board/card/greenhouse:1/verify"' in response.text
    assert "Verify" in response.text
    assert "Open original" in response.text
    assert response.text.index("Verify") < response.text.index("Open original")


def test_replacement_confirmation_uses_server_candidate_and_keeps_original(client):
    candidate = "https://jobs.example.test/replacement"
    _persist(
        client,
        (
            _network(status=404),
            InvestigationObservation(
                sequence=2,
                observed_at=NOW,
                stage=CheckStage.USER_INVESTIGATION,
                target="replacement_candidate",
                attempt=1,
                finding=InvestigationFinding.REPLACEMENT_FOUND,
                candidate_url=candidate,
            ),
        ),
        stages=("queued", "review_finding", "await_user_confirmation"),
    )

    before = client.get("/board/card/greenhouse:1")
    response = client.post(
        "/board/card/greenhouse:1/replacement/confirm",
        data={"candidate_url": "https://attacker.invalid/tampered"},
    )
    conn = db.connect(client.app.state.db_path)
    state = get_state(conn, "greenhouse:1")
    original = conn.execute(
        "SELECT url FROM postings WHERE posting_id = 'greenhouse:1'"
    ).fetchone()[0]
    conn.close()

    assert "Confirm replacement" in before.text
    assert response.status_code == 200
    assert "Replacement confirmed" in response.text
    assert state["confirmed_url"] == candidate
    assert original == "https://x/1"


def test_table_view_exposes_replacement_confirmation_as_primary(client):
    candidate = "https://jobs.example.test/replacement"
    _persist(
        client,
        (
            _network(status=404),
            InvestigationObservation(
                sequence=2,
                observed_at=NOW,
                stage=CheckStage.USER_INVESTIGATION,
                target="replacement_candidate",
                attempt=1,
                finding=InvestigationFinding.REPLACEMENT_FOUND,
                candidate_url=candidate,
            ),
        ),
        stages=("queued", "review_finding", "await_user_confirmation"),
    )

    response = client.get("/board?view=table")

    assert response.status_code == 200
    assert 'action="/board/card/greenhouse:1/replacement/confirm"' in response.text
    assert "Confirm replacement" in response.text
    assert "Open original" in response.text


class _LiveChecker:
    def check(self, url, **kwargs):
        return (
            NetworkObservation(
                sequence=kwargs["sequence"],
                observed_at=kwargs["observed_at"],
                stage=kwargs["stage"],
                target=kwargs["target"],
                attempt=kwargs["attempt"],
                status=200,
                body="<h1>Software Engineer Intern</h1><button>Apply now</button>",
                employer_hosted=True,
            ),
        )


def test_verify_returns_semantic_progress_and_focus_target(client):
    _persist(client, (_network(status=404),))

    def run_offline(conn, posting_id, *, sources_path, now):
        return verify_posting(
            conn,
            posting_id,
            sources_path=sources_path,
            now=now,
            checker=_LiveChecker(),
        )

    client.app.state.availability_verify = run_offline
    response = client.post("/board/card/greenhouse:1/verify")

    assert response.status_code == 200
    assert 'aria-live="polite"' in response.text
    assert 'data-stage="queued"' in response.text
    assert 'data-stage="check_original"' in response.text
    assert 'data-stage="complete"' in response.text
    assert 'id="availability-result-greenhouse-1"' in response.text
    assert ">Apply<" in response.text


def test_verify_confirmed_closed_ordinary_has_accurate_archived_drawer(client):
    conn = db.connect(client.app.state.db_path)
    conn.execute(
        "UPDATE postings SET company = 'Unique Co' WHERE posting_id = 'greenhouse:1'"
    )
    conn.commit()
    conn.close()
    _persist(client, (_network(status=404),))

    class GoneChecker:
        def check(self, url, **kwargs):
            return (
                NetworkObservation(
                    sequence=kwargs["sequence"],
                    observed_at=kwargs["observed_at"],
                    stage=kwargs["stage"],
                    target=kwargs["target"],
                    attempt=kwargs["attempt"],
                    status=410,
                    body="Gone",
                    employer_hosted=True,
                ),
            )

    def run_offline(conn, posting_id, *, sources_path, now):
        return verify_posting(
            conn,
            posting_id,
            sources_path=sources_path,
            now=now,
            checker=GoneChecker(),
        )

    client.app.state.availability_verify = run_offline
    response = client.post("/board/card/greenhouse:1/verify")

    assert response.status_code == 200
    assert "removed from the Board" in response.text
    assert "stays visible because you saved or applied" not in response.text
    assert "Application tracking" not in response.text
    assert "Posting actions" not in response.text
    assert "greenhouse:1" not in client.get("/board").text


@pytest.mark.parametrize("status", ["Interested", "Applied"])
def test_confirmed_closed_protected_role_stays_visible_with_closed_copy(client, status):
    conn = db.connect(client.app.state.db_path)
    store.set_application_status(conn, "greenhouse:1", status)
    conn.close()
    _persist(
        client,
        (
            _network(status=404, sequence=1, attempt=1),
            _network(status=404, sequence=2, attempt=2),
        ),
    )

    board = client.get("/board")
    drawer = client.get("/board/card/greenhouse:1")

    assert "greenhouse:1" in board.text
    assert "Availability closed" in drawer.text
    assert "stays visible because you saved or applied" in drawer.text
    assert "Application tracking" in drawer.text
    assert "Open original" in drawer.text
