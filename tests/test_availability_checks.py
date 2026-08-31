"""Focused production tests for availability observation interpretation."""

from __future__ import annotations

from copy import deepcopy

import pytest

from availability.corpus import load_corpus
from availability.fakes import build_scenario
from availability.production_adapter import create_adapter
from internshelper.availability import (
    AvailabilityStatus,
    BoardTreatment,
    SourceAuthority,
    UserState,
)
from internshelper.availability_checks import (
    CheckStage,
    InvestigationFinding,
    InvestigationObservation,
    NetworkFailure,
    NetworkObservation,
    SourceObservation,
    evaluate_availability,
)


def _network(
    *,
    sequence: int = 1,
    attempt: int = 1,
    status: int | None = 200,
    body: str = "",
    failure: NetworkFailure | None = None,
    employer_hosted: bool = True,
    checker_only: bool = False,
) -> NetworkObservation:
    return NetworkObservation(
        sequence=sequence,
        observed_at=f"T+{sequence:02d}m",
        stage=CheckStage.INITIAL_VALIDATION,
        target="original_url",
        attempt=attempt,
        status=status,
        body=body,
        failure=failure,
        employer_hosted=employer_hosted,
        checker_only=checker_only,
    )


def test_specific_closure_copy_closes_but_generic_error_copy_does_not():
    closed = evaluate_availability(
        source_authority=SourceAuthority.FIRST_PARTY_ATS,
        user_state=UserState.ORDINARY,
        observations=(
            _network(
                body=(
                    "<h1>Software Engineering Intern</h1>"
                    "<p>This position is no longer accepting applications.</p>"
                )
            ),
        ),
    )
    generic = evaluate_availability(
        source_authority=SourceAuthority.FIRST_PARTY_ATS,
        user_state=UserState.ORDINARY,
        observations=(
            _network(body="<h1>Something went wrong</h1><p>Please try again later.</p>"),
        ),
    )

    assert closed.decision.availability is AvailabilityStatus.CLOSED
    assert generic.decision.availability is AvailabilityStatus.UNCERTAIN
    assert generic.decision.board_treatment is BoardTreatment.WARNED


def test_unusual_title_with_apply_control_is_live():
    result = evaluate_availability(
        source_authority=SourceAuthority.FIRST_PARTY_ATS,
        user_state=UserState.ORDINARY,
        observations=(
            _network(
                body=(
                    "<h1>Closed Loop Systems Intern</h1>"
                    "<p>Join the Closed Loop Systems team.</p>"
                    "<button>Apply now</button>"
                )
            ),
        ),
    )

    assert result.decision.availability is AvailabilityStatus.LIVE


def test_hidden_script_closure_copy_cannot_archive_a_visible_live_page():
    result = evaluate_availability(
        source_authority=SourceAuthority.FIRST_PARTY_ATS,
        user_state=UserState.ORDINARY,
        observations=(
            _network(
                body=(
                    "<script>window.copy = 'This position is no longer accepting applications'</script>"
                    "<h1>Software Engineering Intern</h1><button>Apply now</button>"
                )
            ),
        ),
    )

    assert result.decision.availability is AvailabilityStatus.LIVE


def test_checker_barrier_and_user_outage_remain_distinct():
    forbidden = evaluate_availability(
        source_authority=SourceAuthority.FIRST_PARTY_ATS,
        user_state=UserState.ORDINARY,
        observations=(
            _network(
                status=403,
                body="Access denied to automated request",
                checker_only=True,
            ),
        ),
    )
    timeout = evaluate_availability(
        source_authority=SourceAuthority.FIRST_PARTY_ATS,
        user_state=UserState.ORDINARY,
        observations=(
            _network(status=None, failure=NetworkFailure.TIMEOUT),
        ),
    )

    assert forbidden.decision.primary_action.value == "apply"
    assert timeout.decision.primary_action.value == "verify"


def test_login_barrier_on_401_keeps_apply_primary():
    result = evaluate_availability(
        source_authority=SourceAuthority.FIRST_PARTY_ATS,
        user_state=UserState.ORDINARY,
        observations=(
            _network(status=401, body="Sign in to continue your application"),
        ),
    )

    assert result.decision.availability is AvailabilityStatus.UNCERTAIN
    assert result.decision.primary_action.value == "apply"


def test_two_distinct_complete_ats_absences_close_and_same_attempt_does_not():
    def absence(sequence: int, attempt: int) -> SourceObservation:
        return SourceObservation(
            sequence=sequence,
            observed_at=f"T+{sequence:02d}m",
            stage=CheckStage.SCHEDULED_RETRY,
            target="first_party_board",
            attempt=attempt,
            authority=SourceAuthority.FIRST_PARTY_ATS,
            complete=True,
            original_present=False,
        )

    repeated = evaluate_availability(
        source_authority=SourceAuthority.FIRST_PARTY_ATS,
        user_state=UserState.ORDINARY,
        observations=(absence(1, 1), absence(2, 2)),
    )
    duplicate = evaluate_availability(
        source_authority=SourceAuthority.FIRST_PARTY_ATS,
        user_state=UserState.ORDINARY,
        observations=(absence(1, 1), absence(2, 1)),
    )

    assert repeated.decision.availability is AvailabilityStatus.CLOSED
    assert duplicate.decision.availability is AvailabilityStatus.UNCERTAIN


def test_trustworthy_live_and_closed_evidence_is_derived_as_a_conflict():
    result = evaluate_availability(
        source_authority=SourceAuthority.UNKNOWN,
        user_state=UserState.ORDINARY,
        observations=(
            SourceObservation(
                sequence=1,
                observed_at="T+00m",
                stage=CheckStage.INITIAL_VALIDATION,
                target="first_party_board_a",
                attempt=1,
                authority=SourceAuthority.FIRST_PARTY_ATS,
                complete=True,
                original_present=False,
            ),
            SourceObservation(
                sequence=2,
                observed_at="T+10m",
                stage=CheckStage.SCHEDULED_RETRY,
                target="first_party_board_a",
                attempt=2,
                authority=SourceAuthority.FIRST_PARTY_ATS,
                complete=True,
                original_present=False,
            ),
            NetworkObservation(
                sequence=3,
                observed_at="T+10m",
                stage=CheckStage.SCHEDULED_RETRY,
                target="employer_board_b",
                attempt=1,
                status=200,
                body="<h1>Software Engineering Intern</h1><button>Apply now</button>",
                employer_hosted=True,
            ),
        ),
    )

    assert result.decision.availability is AvailabilityStatus.UNCERTAIN
    assert result.decision.board_treatment is BoardTreatment.WARNED
    assert result.decision.primary_action.value == "apply"


def test_trustworthy_conflict_does_not_depend_on_collaborator_call_order():
    result = evaluate_availability(
        source_authority=SourceAuthority.UNKNOWN,
        user_state=UserState.ORDINARY,
        observations=(
            NetworkObservation(
                sequence=1,
                observed_at="T+00m",
                stage=CheckStage.INITIAL_VALIDATION,
                target="employer_board_b",
                attempt=1,
                status=200,
                body="<h1>Software Engineering Intern</h1><button>Apply now</button>",
                employer_hosted=True,
            ),
            SourceObservation(
                sequence=2,
                observed_at="T+00m",
                stage=CheckStage.INITIAL_VALIDATION,
                target="first_party_board_a",
                attempt=1,
                authority=SourceAuthority.FIRST_PARTY_ATS,
                complete=True,
                original_present=False,
            ),
            SourceObservation(
                sequence=3,
                observed_at="T+00m",
                stage=CheckStage.SCHEDULED_RETRY,
                target="first_party_board_a",
                attempt=2,
                authority=SourceAuthority.FIRST_PARTY_ATS,
                complete=True,
                original_present=False,
            ),
        ),
    )

    assert result.decision.availability is AvailabilityStatus.UNCERTAIN
    assert result.decision.primary_action.value == "apply"


def test_first_party_presence_conflicts_with_another_boards_repeated_absence():
    result = evaluate_availability(
        source_authority=SourceAuthority.UNKNOWN,
        user_state=UserState.ORDINARY,
        observations=(
            SourceObservation(
                sequence=1,
                observed_at="T+00m",
                stage=CheckStage.INITIAL_VALIDATION,
                target="first_party_board_a",
                attempt=1,
                authority=SourceAuthority.FIRST_PARTY_ATS,
                complete=True,
                original_present=False,
            ),
            SourceObservation(
                sequence=2,
                observed_at="T+10m",
                stage=CheckStage.SCHEDULED_RETRY,
                target="first_party_board_a",
                attempt=2,
                authority=SourceAuthority.FIRST_PARTY_ATS,
                complete=True,
                original_present=False,
            ),
            SourceObservation(
                sequence=3,
                observed_at="T+10m",
                stage=CheckStage.SCHEDULED_RETRY,
                target="first_party_board_b",
                attempt=1,
                authority=SourceAuthority.FIRST_PARTY_ATS,
                complete=True,
                original_present=True,
            ),
        ),
    )

    assert result.decision.availability is AvailabilityStatus.UNCERTAIN
    assert result.decision.primary_action.value == "apply"


def test_source_boundary_rejects_failed_response_claimed_as_complete_absence():
    with pytest.raises(ValueError, match="failed source observations cannot be complete"):
        SourceObservation(
            sequence=1,
            observed_at="T+00m",
            stage=CheckStage.INITIAL_VALIDATION,
            target="first_party_board",
            attempt=1,
            authority=SourceAuthority.FIRST_PARTY_ATS,
            complete=True,
            original_present=False,
            status=500,
            error="service unavailable",
        )


def test_investigator_replacement_preserves_original_until_confirmation():
    result = evaluate_availability(
        source_authority=SourceAuthority.FIRST_PARTY_ATS,
        user_state=UserState.ORDINARY,
        observations=(
            _network(status=404, body="Page not found"),
            InvestigationObservation(
                sequence=2,
                observed_at="T+02m",
                stage=CheckStage.USER_INVESTIGATION,
                target="replacement_candidate",
                attempt=1,
                finding=InvestigationFinding.REPLACEMENT_FOUND,
                candidate_url="https://jobs.example.test/roles/replacement",
            ),
        ),
        investigation_stages=("queued", "review_finding", "await_user_confirmation"),
    )

    assert result.decision.replacement_requires_confirmation is True
    assert result.decision.stored_url.value == "original"


def test_raw_observation_boundary_rejects_out_of_order_chronology():
    with pytest.raises(ValueError, match="strictly increasing"):
        evaluate_availability(
            source_authority=SourceAuthority.UNKNOWN,
            user_state=UserState.ORDINARY,
            observations=(
                _network(sequence=2, status=404),
                _network(sequence=1, status=404),
            ),
        )


def test_network_adapter_uses_raw_fixtures_not_manifest_signals_or_expectations():
    case = deepcopy(
        next(item for item in load_corpus()["cases"] if item["id"] == "single_404_guarded")
    )
    case["evidence_timeline"][0]["signals"] = ["live_page"]
    case["expected"].update(
        availability="closed",
        board_treatment="archived",
        primary_action="none",
        secondary_action="none",
    )
    scenario = build_scenario(case)

    actual = create_adapter().interpret(case, scenario)

    scenario.assert_consumed()
    assert actual["availability"] == "uncertain"
    assert actual["board_treatment"] == "guarded"
    assert actual["primary_action"] == "verify"


def test_source_adapter_uses_fixture_presence_not_manifest_result():
    case = deepcopy(
        next(
            item
            for item in load_corpus()["cases"]
            if item["id"] == "ats_absence_twice_closed"
        )
    )
    for event in case["evidence_timeline"]:
        event["fixture"] = "source/ats_present.json"
    scenario = build_scenario(case)

    actual = create_adapter().interpret(case, scenario)

    scenario.assert_consumed()
    assert actual["availability"] == "live"
    assert actual["primary_action"] == "apply"
