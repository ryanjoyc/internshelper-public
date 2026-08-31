"""Pure availability policy over normalized posting evidence.

Network clients, source enumerators, persistence, and Board rendering live outside this
module.  They translate external observations into :class:`AvailabilityEvidence`; this
module owns only the user-visible decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SourceAuthority(str, Enum):
    FIRST_PARTY_ATS = "first_party_ats"
    COMMUNITY_LIST = "community_list"
    UNKNOWN = "unknown"


class UserState(str, Enum):
    ORDINARY = "ordinary"
    INTERESTED = "interested"
    APPLIED = "applied"


class EvidenceKind(str, Enum):
    DESTINATION_LIVE = "destination_live"
    DESTINATION_NOT_FOUND = "destination_not_found"
    DESTINATION_GONE = "destination_gone"
    EMPLOYER_CLOSED = "employer_closed"
    FIRST_PARTY_ABSENT = "first_party_absent"
    COMMUNITY_PRESENT = "community_present"
    COMMUNITY_REMOVED = "community_removed"
    AUTOMATION_BARRIER = "automation_barrier"
    USER_OUTAGE = "user_outage"
    TRUSTWORTHY_CONFLICT = "trustworthy_conflict"
    ORIGINAL_RECOVERED = "original_recovered"
    REPLACEMENT_FOUND = "replacement_found"
    CLOSURE_CONFIRMED = "closure_confirmed"
    INVESTIGATION_UNRESOLVED = "investigation_unresolved"


class AvailabilityStatus(str, Enum):
    LIVE = "live"
    UNCERTAIN = "uncertain"
    CLOSED = "closed"


class BoardTreatment(str, Enum):
    NORMAL = "normal"
    WARNED = "warned"
    GUARDED = "guarded"
    ARCHIVED = "archived"
    VISIBLE_CLOSED = "visible_closed"


class AvailabilityAction(str, Enum):
    APPLY = "apply"
    VERIFY = "verify"
    CONFIRM_REPLACEMENT = "confirm_replacement"
    OPEN_ORIGINAL = "open_original"
    NONE = "none"


class InvestigationOutcome(str, Enum):
    NOT_RUN = "not_run"
    ORIGINAL_RECOVERED = "original_recovered"
    REPLACEMENT_FOUND = "replacement_found"
    CLOSURE_CONFIRMED = "closure_confirmed"
    UNRESOLVED = "unresolved"


class ReplacementBehavior(str, Enum):
    NONE = "none"
    OFFER_FOR_CONFIRMATION = "offer_for_confirmation"


class StoredUrlState(str, Enum):
    ORIGINAL = "original"
    CANDIDATE = "candidate"


class RankingEffect(str, Enum):
    """Availability is deliberately not a ranking label."""

    NONE = "none"


_INVESTIGATION_KINDS = {
    EvidenceKind.ORIGINAL_RECOVERED,
    EvidenceKind.REPLACEMENT_FOUND,
    EvidenceKind.CLOSURE_CONFIRMED,
    EvidenceKind.INVESTIGATION_UNRESOLVED,
}


@dataclass(frozen=True)
class EvidenceObservation:
    kind: EvidenceKind
    attempt: int = 1
    candidate_url: str | None = None
    subject: str = "original"

    def __post_init__(self) -> None:
        if self.attempt < 1:
            raise ValueError("evidence attempt must be positive")
        if not self.subject.strip():
            raise ValueError("evidence subject must be non-empty")
        if self.kind is EvidenceKind.REPLACEMENT_FOUND:
            if not self.candidate_url or not self.candidate_url.strip():
                raise ValueError("replacement evidence requires a candidate URL")
        elif self.candidate_url is not None:
            raise ValueError("candidate URL is valid only for replacement evidence")


@dataclass(frozen=True)
class AvailabilityEvidence:
    source_authority: SourceAuthority
    user_state: UserState
    observations: tuple[EvidenceObservation, ...]
    investigation_stages: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.observations:
            raise ValueError("availability policy requires at least one observation")
        outcomes = [item for item in self.observations if item.kind in _INVESTIGATION_KINDS]
        if len(outcomes) > 1:
            raise ValueError("availability evidence contains conflicting investigation outcomes")
        if any(not stage.strip() for stage in self.investigation_stages):
            raise ValueError("investigation stages must be non-empty strings")
        if self.investigation_stages and not outcomes:
            raise ValueError("investigation stages require an investigation outcome")


@dataclass(frozen=True)
class AvailabilityDecision:
    availability: AvailabilityStatus
    board_treatment: BoardTreatment
    primary_action: AvailabilityAction
    secondary_action: AvailabilityAction
    ranking_effect: RankingEffect
    investigation_outcome: InvestigationOutcome
    investigation_stages: tuple[str, ...]
    replacement_behavior: ReplacementBehavior
    replacement_requires_confirmation: bool
    replacement_candidate_url: str | None
    stored_url: StoredUrlState


def decide_availability(evidence: AvailabilityEvidence) -> AvailabilityDecision:
    """Return the conservative user-facing decision for normalized evidence."""

    kinds = {item.kind for item in evidence.observations}
    outcome = _investigation_outcome(kinds)
    replacement_candidate = next(
        (
            item.candidate_url
            for item in evidence.observations
            if item.kind is EvidenceKind.REPLACEMENT_FOUND
        ),
        None,
    )
    status = _availability_status(evidence.observations, kinds, outcome)
    treatment, primary, secondary = _board_decision(
        status=status,
        user_state=evidence.user_state,
        kinds=kinds,
        replacement_candidate=replacement_candidate,
    )

    has_replacement = replacement_candidate is not None
    return AvailabilityDecision(
        availability=status,
        board_treatment=treatment,
        primary_action=primary,
        secondary_action=secondary,
        ranking_effect=RankingEffect.NONE,
        investigation_outcome=outcome,
        investigation_stages=evidence.investigation_stages,
        replacement_behavior=(
            ReplacementBehavior.OFFER_FOR_CONFIRMATION
            if has_replacement
            else ReplacementBehavior.NONE
        ),
        replacement_requires_confirmation=has_replacement,
        replacement_candidate_url=replacement_candidate,
        # Discovering a candidate never mutates the stored destination.  Confirmation is
        # a later user action outside this policy function.
        stored_url=StoredUrlState.ORIGINAL,
    )


def _investigation_outcome(kinds: set[EvidenceKind]) -> InvestigationOutcome:
    mapping = {
        EvidenceKind.ORIGINAL_RECOVERED: InvestigationOutcome.ORIGINAL_RECOVERED,
        EvidenceKind.REPLACEMENT_FOUND: InvestigationOutcome.REPLACEMENT_FOUND,
        EvidenceKind.CLOSURE_CONFIRMED: InvestigationOutcome.CLOSURE_CONFIRMED,
        EvidenceKind.INVESTIGATION_UNRESOLVED: InvestigationOutcome.UNRESOLVED,
    }
    return next(
        (outcome for kind, outcome in mapping.items() if kind in kinds),
        InvestigationOutcome.NOT_RUN,
    )


def _availability_status(
    observations: tuple[EvidenceObservation, ...],
    kinds: set[EvidenceKind],
    outcome: InvestigationOutcome,
) -> AvailabilityStatus:
    # A replacement or explicit trustworthy conflict is never resolved automatically.
    if (
        outcome is InvestigationOutcome.REPLACEMENT_FOUND
        or EvidenceKind.TRUSTWORTHY_CONFLICT in kinds
    ):
        return AvailabilityStatus.UNCERTAIN

    if EvidenceKind.EMPLOYER_CLOSED in kinds or outcome is InvestigationOutcome.CLOSURE_CONFIRMED:
        return AvailabilityStatus.CLOSED

    attempts_by_kind_and_subject = {
        (kind, subject): {
            item.attempt
            for item in observations
            if item.kind is kind and item.subject == subject
        }
        for kind in EvidenceKind
        for subject in {item.subject for item in observations if item.kind is kind}
    }
    consistent_hard_failure = any(
        len(attempts) >= 2
        for (kind, _subject), attempts in attempts_by_kind_and_subject.items()
        if kind in (EvidenceKind.DESTINATION_NOT_FOUND, EvidenceKind.DESTINATION_GONE)
    )
    repeated_first_party_absence = any(
        len(attempts) >= 2
        for (kind, _subject), attempts in attempts_by_kind_and_subject.items()
        if kind is EvidenceKind.FIRST_PARTY_ABSENT
    )
    if consistent_hard_failure or repeated_first_party_absence:
        return AvailabilityStatus.CLOSED

    if outcome is InvestigationOutcome.ORIGINAL_RECOVERED or EvidenceKind.DESTINATION_LIVE in kinds:
        return AvailabilityStatus.LIVE

    # Barriers, outages, one hard failure, one complete ATS absence, and community-only
    # observations all lack enough role-specific evidence to prove closure or liveness.
    return AvailabilityStatus.UNCERTAIN


def _board_decision(
    *,
    status: AvailabilityStatus,
    user_state: UserState,
    kinds: set[EvidenceKind],
    replacement_candidate: str | None,
) -> tuple[BoardTreatment, AvailabilityAction, AvailabilityAction]:
    if status is AvailabilityStatus.LIVE:
        return BoardTreatment.NORMAL, AvailabilityAction.APPLY, AvailabilityAction.NONE

    if status is AvailabilityStatus.CLOSED:
        if user_state in {UserState.INTERESTED, UserState.APPLIED}:
            return (
                BoardTreatment.VISIBLE_CLOSED,
                AvailabilityAction.NONE,
                AvailabilityAction.OPEN_ORIGINAL,
            )
        return BoardTreatment.ARCHIVED, AvailabilityAction.NONE, AvailabilityAction.NONE

    if replacement_candidate is not None:
        return (
            BoardTreatment.GUARDED,
            AvailabilityAction.CONFIRM_REPLACEMENT,
            AvailabilityAction.OPEN_ORIGINAL,
        )

    if kinds & {EvidenceKind.DESTINATION_NOT_FOUND, EvidenceKind.DESTINATION_GONE}:
        return (
            BoardTreatment.GUARDED,
            AvailabilityAction.VERIFY,
            AvailabilityAction.OPEN_ORIGINAL,
        )

    if EvidenceKind.TRUSTWORTHY_CONFLICT in kinds and EvidenceKind.DESTINATION_LIVE in kinds:
        return BoardTreatment.WARNED, AvailabilityAction.APPLY, AvailabilityAction.VERIFY

    if EvidenceKind.FIRST_PARTY_ABSENT in kinds:
        return BoardTreatment.WARNED, AvailabilityAction.APPLY, AvailabilityAction.VERIFY

    if EvidenceKind.AUTOMATION_BARRIER in kinds and EvidenceKind.USER_OUTAGE not in kinds:
        return BoardTreatment.NORMAL, AvailabilityAction.APPLY, AvailabilityAction.VERIFY

    return (
        BoardTreatment.WARNED,
        AvailabilityAction.VERIFY,
        AvailabilityAction.OPEN_ORIGINAL,
    )
