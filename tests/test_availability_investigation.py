"""Semantic, transport-independent investigation workflow tests."""

from __future__ import annotations

import pytest

from internshelper.availability import SourceAuthority, UserState
from internshelper.availability_checks import (
    CheckStage,
    InvestigationFinding,
    InvestigationObservation,
    NetworkObservation,
)
from internshelper.availability_investigation import (
    InvestigationStage,
    conclude_investigation,
)


def test_replacement_report_keeps_semantic_progress_and_requires_confirmation():
    observations = (
        NetworkObservation(
            sequence=1,
            observed_at="2026-08-30T12:01:00+00:00",
            stage=CheckStage.USER_INVESTIGATION,
            target="original_url",
            attempt=1,
            status=404,
            body="Page not found",
            employer_hosted=True,
        ),
        InvestigationObservation(
            sequence=2,
            observed_at="2026-08-30T12:02:00+00:00",
            stage=CheckStage.USER_INVESTIGATION,
            target="replacement_candidate",
            attempt=1,
            finding=InvestigationFinding.REPLACEMENT_FOUND,
            candidate_url="https://jobs.example.test/roles/replacement",
        ),
    )
    stages = (
        InvestigationStage.QUEUED,
        InvestigationStage.CHECK_ORIGINAL,
        InvestigationStage.REVIEW_FINDING,
        InvestigationStage.AWAIT_USER_CONFIRMATION,
    )

    report = conclude_investigation(
        source_authority=SourceAuthority.FIRST_PARTY_ATS,
        user_state=UserState.ORDINARY,
        observations=observations,
        stages=stages,
    )

    assert report.stages == stages
    assert report.evaluation.decision.replacement_requires_confirmation is True
    assert report.evaluation.decision.stored_url.value == "original"


def test_nonreplacement_investigation_must_finish_with_complete():
    observation = InvestigationObservation(
        sequence=1,
        observed_at="2026-08-30T12:01:00+00:00",
        stage=CheckStage.USER_INVESTIGATION,
        target="original_url",
        attempt=1,
        finding=InvestigationFinding.UNRESOLVED,
    )

    with pytest.raises(ValueError, match="complete"):
        conclude_investigation(
            source_authority=SourceAuthority.UNKNOWN,
            user_state=UserState.ORDINARY,
            observations=(observation,),
            stages=(InvestigationStage.QUEUED, InvestigationStage.CHECK_ORIGINAL),
        )


def test_progress_must_start_queued_and_use_unique_semantic_stages():
    observation = InvestigationObservation(
        sequence=1,
        observed_at="2026-08-30T12:01:00+00:00",
        stage=CheckStage.USER_INVESTIGATION,
        target="original_url",
        attempt=1,
        finding=InvestigationFinding.UNRESOLVED,
    )

    with pytest.raises(ValueError, match="queued"):
        conclude_investigation(
            source_authority=SourceAuthority.UNKNOWN,
            user_state=UserState.ORDINARY,
            observations=(observation,),
            stages=(InvestigationStage.CHECK_ORIGINAL, InvestigationStage.COMPLETE),
        )
