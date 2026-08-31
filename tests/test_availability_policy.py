"""Small default-running checks around the production availability boundary."""

from copy import deepcopy

import pytest

from availability.corpus import load_corpus
from availability.production_adapter import create_adapter
from internshelper.availability import (
    AvailabilityEvidence,
    AvailabilityStatus,
    EvidenceKind,
    EvidenceObservation,
    SourceAuthority,
    UserState,
    decide_availability,
)


def test_policy_adapter_derives_decisions_instead_of_copying_expected_values():
    case = deepcopy(
        next(case for case in load_corpus()["cases"] if case["id"] == "single_404_guarded")
    )
    case["expected"].update(
        availability="closed",
        board_treatment="archived",
        primary_action="none",
        secondary_action="none",
        ranking_effect="negative",
    )

    actual = create_adapter().decide(case)

    assert actual["availability"] == "uncertain"
    assert actual["board_treatment"] == "guarded"
    assert actual["primary_action"] == "verify"
    assert actual["ranking_effect"] == "none"


def test_policy_boundary_rejects_empty_evidence():
    with pytest.raises(ValueError, match="at least one observation"):
        AvailabilityEvidence(
            source_authority=SourceAuthority.FIRST_PARTY_ATS,
            user_state=UserState.ORDINARY,
            observations=(),
        )


def test_policy_boundary_rejects_conflicting_investigation_outcomes():
    with pytest.raises(ValueError, match="conflicting investigation outcomes"):
        AvailabilityEvidence(
            source_authority=SourceAuthority.UNKNOWN,
            user_state=UserState.ORDINARY,
            observations=(
                EvidenceObservation(EvidenceKind.ORIGINAL_RECOVERED),
                EvidenceObservation(EvidenceKind.INVESTIGATION_UNRESOLVED),
            ),
            investigation_stages=("queued", "complete"),
        )


def test_duplicate_observations_from_one_attempt_do_not_confirm_closure():
    evidence = AvailabilityEvidence(
        source_authority=SourceAuthority.FIRST_PARTY_ATS,
        user_state=UserState.ORDINARY,
        observations=(
            EvidenceObservation(EvidenceKind.DESTINATION_NOT_FOUND, attempt=1),
            EvidenceObservation(EvidenceKind.DESTINATION_NOT_FOUND, attempt=1),
        ),
    )

    assert decide_availability(evidence).availability is AvailabilityStatus.UNCERTAIN


def test_failures_on_different_destinations_do_not_confirm_closure():
    evidence = AvailabilityEvidence(
        source_authority=SourceAuthority.FIRST_PARTY_ATS,
        user_state=UserState.ORDINARY,
        observations=(
            EvidenceObservation(
                EvidenceKind.DESTINATION_NOT_FOUND,
                attempt=1,
                subject="original_url",
            ),
            EvidenceObservation(
                EvidenceKind.DESTINATION_NOT_FOUND,
                attempt=2,
                subject="replacement_candidate",
            ),
        ),
    )

    assert decide_availability(evidence).availability is AvailabilityStatus.UNCERTAIN
