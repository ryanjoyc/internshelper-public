"""Safe temporary-database tests for the user-triggered verification service."""

from __future__ import annotations

from internshelper import db, store
from internshelper.availability import SourceAuthority, UserState
from internshelper.availability_checks import (
    CheckStage,
    NetworkObservation,
    SourceObservation,
    evaluate_availability,
)
from internshelper.availability_service import verify_posting
from internshelper.availability_store import (
    evidence_history,
    get_state,
    persist_evaluation,
)
from internshelper.models import Posting


NOW = "2026-08-30T12:00:00+00:00"


class _Checker:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = []

    def check(self, url, **kwargs):
        self.calls.append((url, kwargs))
        status = self.statuses.pop(0)
        return (
            NetworkObservation(
                sequence=kwargs["sequence"],
                observed_at=kwargs["observed_at"],
                stage=kwargs["stage"],
                target=kwargs["target"],
                attempt=kwargs["attempt"],
                status=status,
                body=(
                    "<h1>Software Engineering Intern</h1><button>Apply now</button>"
                    if status == 200
                    else "Gone"
                ),
                employer_hosted=kwargs["employer_hosted"],
            ),
        )


def _setup(tmp_path, *, candidate=False, source_key="greenhouse:example"):
    conn = db.connect(tmp_path / "service.db")
    db.init_db(conn)
    original = Posting(
        posting_id="greenhouse:original",
        source_key=source_key,
        title="Software Engineering Intern",
        company="Example Co",
        url="https://jobs.example.test/original",
    )
    store.upsert(conn, original, now=NOW)
    if candidate:
        store.upsert(
            conn,
            Posting(
                posting_id="greenhouse:candidate",
                source_key="greenhouse:other",
                title="Software Engineering Internship",
                company="Example Co",
                url="https://jobs.example.test/replacement",
            ),
            now=NOW,
        )
    sources = tmp_path / "sources.yaml"
    sources.write_text("sources:\n")
    return conn, original, sources


def test_original_recovery_completes_with_apply(tmp_path):
    conn, original, sources = _setup(tmp_path)

    result = verify_posting(
        conn,
        original.posting_id,
        sources_path=str(sources),
        now=NOW,
        checker=_Checker([200]),
    )

    assert result.report.evaluation.decision.investigation_outcome.value == "original_recovered"
    assert result.projection.decision.primary_action.value == "apply"
    assert [stage.value for stage in result.report.stages] == [
        "queued", "check_original", "complete"
    ]


def test_two_consistent_gone_responses_confirm_closure(tmp_path):
    conn, original, sources = _setup(tmp_path)

    result = verify_posting(
        conn,
        original.posting_id,
        sources_path=str(sources),
        now=NOW,
        checker=_Checker([410, 410]),
    )

    assert result.report.evaluation.decision.investigation_outcome.value == "closure_confirmed"
    assert result.projection.visible is False


def test_database_replacement_is_offered_without_changing_original_url(tmp_path):
    conn, original, sources = _setup(tmp_path, candidate=True)

    result = verify_posting(
        conn,
        original.posting_id,
        sources_path=str(sources),
        now=NOW,
        checker=_Checker([404, 404]),
    )

    state = get_state(conn, original.posting_id)
    assert result.report.evaluation.decision.investigation_outcome.value == "replacement_found"
    assert state["pending_candidate_url"] == "https://jobs.example.test/replacement"
    assert state["confirmed_url"] is None
    assert conn.execute(
        "SELECT url FROM postings WHERE posting_id = ?", (original.posting_id,)
    ).fetchone()[0] == original.url


def test_verify_preserves_persisted_sequence_and_attempt_identity(tmp_path):
    conn, original, sources = _setup(tmp_path)
    initial = evaluate_availability(
        source_authority=SourceAuthority.FIRST_PARTY_ATS,
        user_state=UserState.ORDINARY,
        observations=(
            NetworkObservation(
                sequence=12,
                observed_at="2026-08-30T11:00:00+00:00",
                stage=CheckStage.SCHEDULED_RETRY,
                target="original_url",
                attempt=7,
                status=404,
                body="Not found",
                employer_hosted=True,
            ),
        ),
    )
    persist_evaluation(conn, original.posting_id, initial, updated_at=NOW)
    checker = _Checker([200])

    verify_posting(
        conn,
        original.posting_id,
        sources_path=str(sources),
        now=NOW,
        checker=checker,
    )

    assert checker.calls[0][1]["sequence"] == 13
    assert checker.calls[0][1]["attempt"] == 8
    assert get_state(conn, original.posting_id)["last_attempt"] == 8
    current = [row for row in evidence_history(conn, original.posting_id) if row["is_current"]]
    assert min(row["sequence"] for row in current) == 12
    assert max(row["sequence"] for row in current) == 14


def test_hard_failures_on_different_targets_do_not_confirm_closure(tmp_path):
    conn, original, sources = _setup(tmp_path)

    class Checker:
        def __init__(self):
            self.call = 0

        def check(self, url, **kwargs):
            self.call += 1
            return (
                NetworkObservation(
                    sequence=kwargs["sequence"],
                    observed_at=kwargs["observed_at"],
                    stage=kwargs["stage"],
                    target=f"redirect_target_{self.call}",
                    attempt=kwargs["attempt"],
                    status=404,
                    body="Not found",
                    employer_hosted=True,
                ),
            )

    result = verify_posting(
        conn,
        original.posting_id,
        sources_path=str(sources),
        now=NOW,
        checker=Checker(),
    )

    assert result.report.evaluation.decision.investigation_outcome.value == "unresolved"
    assert result.projection.decision.availability.value == "uncertain"


def test_community_destination_is_not_asserted_to_be_employer_hosted(tmp_path):
    conn, original, sources = _setup(
        tmp_path, source_key="markdown:https://lists.example.test/README.md"
    )

    class Checker:
        def check(self, url, **kwargs):
            assert kwargs["employer_hosted"] is False
            return (
                NetworkObservation(
                    sequence=kwargs["sequence"],
                    observed_at=kwargs["observed_at"],
                    stage=kwargs["stage"],
                    target=kwargs["target"],
                    attempt=kwargs["attempt"],
                    status=200,
                    body="This job is no longer accepting applications",
                    employer_hosted=kwargs["employer_hosted"],
                ),
            )

    result = verify_posting(
        conn,
        original.posting_id,
        sources_path=str(sources),
        now=NOW,
        checker=Checker(),
    )

    assert result.report.evaluation.decision.investigation_outcome.value == "unresolved"
    assert result.projection.decision.availability.value == "uncertain"


def test_single_410_plus_outage_and_single_absence_remains_unresolved(tmp_path):
    conn, original, sources = _setup(tmp_path)

    class Provider:
        def gather(self, conn, row, **kwargs):
            return (
                NetworkObservation(
                    sequence=1,
                    observed_at=NOW,
                    stage=CheckStage.USER_INVESTIGATION,
                    target="original_url",
                    attempt=1,
                    status=410,
                    body="Gone",
                    employer_hosted=True,
                ),
                NetworkObservation(
                    sequence=2,
                    observed_at=NOW,
                    stage=CheckStage.USER_INVESTIGATION,
                    target="original_url",
                    attempt=2,
                    status=503,
                    body="Service unavailable",
                    employer_hosted=True,
                ),
                SourceObservation(
                    sequence=3,
                    observed_at=NOW,
                    stage=CheckStage.USER_INVESTIGATION,
                    target="greenhouse:example",
                    attempt=1,
                    authority=SourceAuthority.FIRST_PARTY_ATS,
                    complete=True,
                    original_present=False,
                ),
            )

    result = verify_posting(
        conn,
        original.posting_id,
        sources_path=str(sources),
        now=NOW,
        checker=_Checker([]),
        evidence_provider=Provider(),
    )

    assert result.report.evaluation.decision.investigation_outcome.value == "unresolved"
    assert result.projection.decision.availability.value == "uncertain"
