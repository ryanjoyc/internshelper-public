"""Semantic availability-investigation progress and completion.

This module knows neither FastAPI nor a progress transport.  A synchronous route,
polling job, or future SSE worker can emit the same stages and conclude with the same
typed observation history.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from internshelper.availability import SourceAuthority, UserState
from internshelper.availability_checks import (
    AvailabilityEvaluation,
    InterpretedObservation,
    InvestigationFinding,
    InvestigationObservation,
    RawObservation,
    evaluate_availability,
)


class InvestigationStage(str, Enum):
    QUEUED = "queued"
    CHECK_ORIGINAL = "check_original"
    RETRY_ORIGINAL = "retry_original"
    ENUMERATE_FIRST_PARTY = "enumerate_first_party"
    COMPARE_SOURCES = "compare_sources"
    SEARCH_REPLACEMENT = "search_replacement"
    REVIEW_FINDING = "review_finding"
    AWAIT_USER_CONFIRMATION = "await_user_confirmation"
    COMPLETE = "complete"


STAGE_LABELS = {
    InvestigationStage.QUEUED: "Queued",
    InvestigationStage.CHECK_ORIGINAL: "Checking the original link",
    InvestigationStage.RETRY_ORIGINAL: "Retrying the original link",
    InvestigationStage.ENUMERATE_FIRST_PARTY: "Enumerating the first-party source",
    InvestigationStage.COMPARE_SOURCES: "Comparing available sources",
    InvestigationStage.SEARCH_REPLACEMENT: "Searching for a replacement",
    InvestigationStage.REVIEW_FINDING: "Reviewing the finding",
    InvestigationStage.AWAIT_USER_CONFIRMATION: "Awaiting your confirmation",
    InvestigationStage.COMPLETE: "Complete",
}


@dataclass(frozen=True)
class InvestigationReport:
    stages: tuple[InvestigationStage, ...]
    evaluation: AvailabilityEvaluation

    @property
    def stage_items(self) -> tuple[dict[str, str], ...]:
        return tuple(
            {"key": stage.value, "label": STAGE_LABELS[stage]} for stage in self.stages
        )


def _validate_stages(
    stages: tuple[InvestigationStage, ...], finding: InvestigationFinding
) -> None:
    if not stages or stages[0] is not InvestigationStage.QUEUED:
        raise ValueError("investigation progress must start with queued")
    if len(stages) != len(set(stages)):
        raise ValueError("investigation progress stages must be unique")
    if finding is InvestigationFinding.REPLACEMENT_FOUND:
        if stages[-1] is not InvestigationStage.AWAIT_USER_CONFIRMATION:
            raise ValueError("replacement progress must await user confirmation")
    elif stages[-1] is not InvestigationStage.COMPLETE:
        raise ValueError("non-replacement investigation progress must end with complete")


def conclude_investigation(
    *,
    source_authority: SourceAuthority,
    user_state: UserState,
    observations: tuple[RawObservation, ...],
    stages: tuple[InvestigationStage, ...],
    previous_records: tuple[InterpretedObservation, ...] = (),
) -> InvestigationReport:
    """Validate semantic progress and reduce the investigator's final evidence."""

    findings = [
        item for item in observations if isinstance(item, InvestigationObservation)
    ]
    if len(findings) != 1:
        raise ValueError("an investigation requires exactly one final finding")
    _validate_stages(stages, findings[0].finding)
    evaluation = evaluate_availability(
        source_authority=source_authority,
        user_state=user_state,
        observations=observations,
        investigation_stages=tuple(stage.value for stage in stages),
        previous_records=previous_records,
    )
    return InvestigationReport(stages=stages, evaluation=evaluation)
