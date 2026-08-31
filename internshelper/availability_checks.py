"""Interpret external availability observations without performing I/O.

HTTP clients, connectors, and investigation transports stop at the typed raw
observations in this module.  The interpreter validates those boundary values,
retains their chronology for persistence, and emits only normalized evidence to
the pure policy reducer in :mod:`internshelper.availability`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias
from urllib.parse import urlparse

from internshelper.availability import (
    AvailabilityDecision,
    AvailabilityEvidence,
    EvidenceKind,
    EvidenceObservation,
    SourceAuthority,
    UserState,
    decide_availability,
)
from internshelper.text import strip_html


class CheckStage(str, Enum):
    INITIAL_VALIDATION = "initial_validation"
    SCHEDULED_RETRY = "scheduled_retry"
    USER_INVESTIGATION = "user_investigation"


class ObservationChannel(str, Enum):
    NETWORK = "network"
    SOURCE = "source"
    INVESTIGATOR = "investigator"
    RECONCILIATION = "reconciliation"


class NetworkFailure(str, Enum):
    TIMEOUT = "timeout"
    DNS = "dns_failure"
    CONNECTION = "connection_failure"
    REDIRECT_LOOP = "redirect_loop"
    UNSAFE_DESTINATION = "unsafe_destination"


class InvestigationFinding(str, Enum):
    ORIGINAL_RECOVERED = "original_recovered"
    REPLACEMENT_FOUND = "replacement_found"
    CLOSURE_CONFIRMED = "closure_confirmed"
    UNRESOLVED = "unresolved"


def _validate_common(
    *, sequence: int, observed_at: str, target: str, attempt: int
) -> None:
    if sequence < 1:
        raise ValueError("observation sequence must be positive")
    if attempt < 1:
        raise ValueError("observation attempt must be positive")
    if not observed_at.strip():
        raise ValueError("observation time must be non-empty")
    if not target.strip():
        raise ValueError("observation target must be non-empty")


def _validate_url(value: str, *, label: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{label} must be an absolute HTTP(S) URL")


@dataclass(frozen=True)
class NetworkObservation:
    sequence: int
    observed_at: str
    stage: CheckStage
    target: str
    attempt: int
    status: int | None = None
    body: str = ""
    location: str | None = None
    failure: NetworkFailure | None = None
    employer_hosted: bool = False
    checker_only: bool = False

    def __post_init__(self) -> None:
        _validate_common(
            sequence=self.sequence,
            observed_at=self.observed_at,
            target=self.target,
            attempt=self.attempt,
        )
        if self.status is None and self.failure is None:
            raise ValueError("network observation requires a status or transport failure")
        if self.status is not None and not 100 <= self.status <= 599:
            raise ValueError("network status must be between 100 and 599")
        if self.status is not None and self.failure is not None:
            raise ValueError("network status and transport failure are mutually exclusive")
        if self.location is not None:
            _validate_url(self.location, label="redirect location")


@dataclass(frozen=True)
class SourceObservation:
    sequence: int
    observed_at: str
    stage: CheckStage
    target: str
    attempt: int
    authority: SourceAuthority
    complete: bool
    original_present: bool | None
    candidate_url: str | None = None
    status: int | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        _validate_common(
            sequence=self.sequence,
            observed_at=self.observed_at,
            target=self.target,
            attempt=self.attempt,
        )
        if self.complete and self.original_present is None:
            raise ValueError("complete source observations require a presence result")
        failed = bool(self.error) or bool(self.status is not None and self.status >= 400)
        if self.complete and failed:
            raise ValueError("failed source observations cannot be complete")
        if not self.complete and self.original_present is not None:
            raise ValueError("incomplete source observations cannot claim presence")
        if self.status is not None and not 100 <= self.status <= 599:
            raise ValueError("source status must be between 100 and 599")
        if self.candidate_url is not None:
            _validate_url(self.candidate_url, label="replacement candidate")


@dataclass(frozen=True)
class InvestigationObservation:
    sequence: int
    observed_at: str
    stage: CheckStage
    target: str
    attempt: int
    finding: InvestigationFinding
    candidate_url: str | None = None

    def __post_init__(self) -> None:
        _validate_common(
            sequence=self.sequence,
            observed_at=self.observed_at,
            target=self.target,
            attempt=self.attempt,
        )
        if self.finding is InvestigationFinding.REPLACEMENT_FOUND:
            if self.candidate_url is None:
                raise ValueError("replacement findings require a candidate URL")
            _validate_url(self.candidate_url, label="replacement candidate")
        elif self.candidate_url is not None:
            raise ValueError("candidate URL is valid only for replacement findings")


RawObservation: TypeAlias = (
    NetworkObservation | SourceObservation | InvestigationObservation
)


@dataclass(frozen=True)
class InterpretedObservation:
    """One chronological record; ``kind=None`` denotes supporting evidence."""

    sequence: int
    observed_at: str
    stage: CheckStage
    channel: ObservationChannel
    target: str
    attempt: int
    signal: str
    kind: EvidenceKind | None
    status: int | None = None
    location: str | None = None
    candidate_url: str | None = None
    authority: SourceAuthority | None = None
    trustworthy: bool = False
    detail: str | None = None

    def policy_observation(self) -> EvidenceObservation | None:
        if self.kind is None:
            return None
        return EvidenceObservation(
            kind=self.kind,
            attempt=self.attempt,
            candidate_url=self.candidate_url,
            subject=self.target,
        )


@dataclass(frozen=True)
class AvailabilityEvaluation:
    records: tuple[InterpretedObservation, ...]
    evidence: AvailabilityEvidence
    decision: AvailabilityDecision


_EMPLOYER_CLOSURE_PHRASES = (
    "this position is no longer accepting applications",
    "this job is no longer accepting applications",
    "this position has been filled",
    "this job has been filled",
)
_AUTOMATION_PATTERNS = (
    "checking your browser",
    "verify that you are human",
    "javascript is required",
    "enable javascript",
    "sign in to continue your application",
    "automated request was denied",
)
_OUTAGE_PATTERNS = (
    "scheduled maintenance",
    "site maintenance",
    "temporary maintenance",
    "undergoing temporary maintenance",
    "service unavailable",
    "temporarily unavailable",
    "something went wrong",
    "could not complete your request",
    "please try again later",
    "we will be back soon",
)
_LIVE_PATTERNS = (
    "apply now",
    "start application",
    "submit application",
    "begin application",
)


def _record(
    observation: RawObservation,
    *,
    channel: ObservationChannel,
    signal: str,
    kind: EvidenceKind | None,
    status: int | None = None,
    location: str | None = None,
    candidate_url: str | None = None,
    authority: SourceAuthority | None = None,
    trustworthy: bool = False,
    detail: str | None = None,
) -> InterpretedObservation:
    return InterpretedObservation(
        sequence=observation.sequence,
        observed_at=observation.observed_at,
        stage=observation.stage,
        channel=channel,
        target=observation.target,
        attempt=observation.attempt,
        signal=signal,
        kind=kind,
        status=status,
        location=location,
        candidate_url=candidate_url,
        authority=authority,
        trustworthy=trustworthy,
        detail=detail,
    )


def interpret_network(observation: NetworkObservation) -> InterpretedObservation:
    """Classify one response/failure without guessing from a job title."""

    if observation.failure is not None:
        return _record(
            observation,
            channel=ObservationChannel.NETWORK,
            signal=observation.failure.value,
            kind=EvidenceKind.USER_OUTAGE,
            trustworthy=False,
        )

    assert observation.status is not None
    status = observation.status
    common = {
        "channel": ObservationChannel.NETWORK,
        "status": status,
        "authority": (
            SourceAuthority.FIRST_PARTY_ATS if observation.employer_hosted else None
        ),
    }
    if status == 404:
        return _record(
            observation,
            signal="http_404",
            kind=EvidenceKind.DESTINATION_NOT_FOUND,
            trustworthy=observation.employer_hosted,
            **common,
        )
    if status == 410:
        return _record(
            observation,
            signal="http_410",
            kind=EvidenceKind.DESTINATION_GONE,
            trustworthy=observation.employer_hosted,
            **common,
        )
    if 300 <= status <= 399:
        return _record(
            observation,
            signal="redirect" if observation.location else "invalid_redirect",
            kind=None if observation.location else EvidenceKind.USER_OUTAGE,
            location=observation.location,
            trustworthy=False,
            **common,
        )
    visible = re.sub(r"\s+", " ", strip_html(observation.body).lower()).strip()
    if status in {401, 403} and (
        observation.checker_only
        or any(pattern in visible for pattern in _AUTOMATION_PATTERNS)
    ):
        return _record(
            observation,
            signal="checker_barrier",
            kind=EvidenceKind.AUTOMATION_BARRIER,
            trustworthy=False,
            **common,
        )
    if status == 429 or status >= 500 or status >= 400:
        return _record(
            observation,
            signal=f"http_{status}",
            kind=EvidenceKind.USER_OUTAGE,
            trustworthy=False,
            **common,
        )

    if observation.employer_hosted and any(
        phrase in visible for phrase in _EMPLOYER_CLOSURE_PHRASES
    ):
        return _record(
            observation,
            signal="employer_closure_text",
            kind=EvidenceKind.EMPLOYER_CLOSED,
            trustworthy=True,
            **common,
        )
    if any(pattern in visible for pattern in _AUTOMATION_PATTERNS):
        return _record(
            observation,
            signal="automation_barrier",
            kind=EvidenceKind.AUTOMATION_BARRIER,
            trustworthy=False,
            **common,
        )
    if any(pattern in visible for pattern in _OUTAGE_PATTERNS):
        return _record(
            observation,
            signal="user_outage_page",
            kind=EvidenceKind.USER_OUTAGE,
            trustworthy=False,
            **common,
        )
    if any(pattern in visible for pattern in _LIVE_PATTERNS) or (
        "data-posting-id" in observation.body.lower()
        and ("<button" in observation.body.lower() or "<form" in observation.body.lower())
    ):
        return _record(
            observation,
            signal="live_job_page",
            kind=EvidenceKind.DESTINATION_LIVE,
            trustworthy=observation.employer_hosted,
            **common,
        )
    return _record(
        observation,
        signal="unrecognized_response",
        kind=EvidenceKind.USER_OUTAGE,
        trustworthy=False,
        **common,
    )


def interpret_source(observation: SourceObservation) -> InterpretedObservation:
    """Interpret only complete enumerations as role presence/absence evidence."""

    common = {
        "channel": ObservationChannel.SOURCE,
        "status": observation.status,
        "authority": observation.authority,
    }
    if not observation.complete:
        return _record(
            observation,
            signal="source_error",
            kind=EvidenceKind.USER_OUTAGE,
            trustworthy=False,
            detail=observation.error,
            **common,
        )
    if observation.candidate_url is not None:
        return _record(
            observation,
            signal="replacement_candidate_present",
            kind=None,
            candidate_url=observation.candidate_url,
            trustworthy=observation.authority is SourceAuthority.FIRST_PARTY_ATS,
            **common,
        )

    if observation.authority is SourceAuthority.FIRST_PARTY_ATS:
        return _record(
            observation,
            signal=("first_party_present" if observation.original_present else "first_party_absent"),
            kind=(
                EvidenceKind.DESTINATION_LIVE
                if observation.original_present
                else EvidenceKind.FIRST_PARTY_ABSENT
            ),
            trustworthy=True,
            **common,
        )
    if observation.authority is SourceAuthority.COMMUNITY_LIST:
        return _record(
            observation,
            signal=("community_present" if observation.original_present else "community_removed"),
            kind=(
                EvidenceKind.COMMUNITY_PRESENT
                if observation.original_present
                else EvidenceKind.COMMUNITY_REMOVED
            ),
            trustworthy=False,
            **common,
        )
    return _record(
        observation,
        signal="unknown_source_observation",
        kind=None,
        trustworthy=False,
        **common,
    )


def interpret_investigation(
    observation: InvestigationObservation,
) -> InterpretedObservation:
    kinds = {
        InvestigationFinding.ORIGINAL_RECOVERED: EvidenceKind.ORIGINAL_RECOVERED,
        InvestigationFinding.REPLACEMENT_FOUND: EvidenceKind.REPLACEMENT_FOUND,
        InvestigationFinding.CLOSURE_CONFIRMED: EvidenceKind.CLOSURE_CONFIRMED,
        InvestigationFinding.UNRESOLVED: EvidenceKind.INVESTIGATION_UNRESOLVED,
    }
    return _record(
        observation,
        channel=ObservationChannel.INVESTIGATOR,
        signal=observation.finding.value,
        kind=kinds[observation.finding],
        candidate_url=observation.candidate_url,
        trustworthy=observation.finding is not InvestigationFinding.UNRESOLVED,
    )


def _closure_grade_subjects(
    records: list[InterpretedObservation],
) -> dict[str, int]:
    subjects = {
        record.target: record.sequence
        for record in records
        if record.trustworthy
        and record.kind in {EvidenceKind.EMPLOYER_CLOSED, EvidenceKind.CLOSURE_CONFIRMED}
    }
    attempts: dict[tuple[EvidenceKind, str], dict[int, int]] = {}
    for record in records:
        if not record.trustworthy or record.kind not in {
            EvidenceKind.DESTINATION_NOT_FOUND,
            EvidenceKind.DESTINATION_GONE,
            EvidenceKind.FIRST_PARTY_ABSENT,
        }:
            continue
        attempts.setdefault((record.kind, record.target), {})[
            record.attempt
        ] = record.sequence
    for (_kind, subject), values in attempts.items():
        if len(values) >= 2:
            subjects[subject] = max(subjects.get(subject, 0), max(values.values()))
    return subjects


def _append_conflict_if_needed(
    records: list[InterpretedObservation],
) -> None:
    live_subjects = {
        record.target: record.sequence
        for record in records
        if record.kind is EvidenceKind.DESTINATION_LIVE and record.trustworthy
    }
    closed_subjects = _closure_grade_subjects(records)
    if not any(
        live_subject != closed_subject
        for live_subject in live_subjects
        for closed_subject in closed_subjects
    ):
        return
    last = records[-1]
    records.append(
        InterpretedObservation(
            sequence=last.sequence,
            observed_at=last.observed_at,
            stage=last.stage,
            channel=ObservationChannel.RECONCILIATION,
            target="trustworthy_sources",
            attempt=last.attempt,
            signal="trustworthy_conflict",
            kind=EvidenceKind.TRUSTWORTHY_CONFLICT,
            trustworthy=True,
        )
    )


def evaluate_interpreted_availability(
    *,
    source_authority: SourceAuthority,
    user_state: UserState,
    records: tuple[InterpretedObservation, ...],
    investigation_stages: tuple[str, ...] = (),
) -> AvailabilityEvaluation:
    """Reduce validated normalized records, including cross-source reconciliation."""

    if not records:
        raise ValueError("availability evaluation requires at least one record")
    reconciled = [
        record
        for record in records
        if record.channel is not ObservationChannel.RECONCILIATION
    ]
    if not reconciled:
        raise ValueError("availability evaluation requires non-reconciliation evidence")
    _append_conflict_if_needed(reconciled)
    policy_observations = tuple(
        item
        for record in reconciled
        if (item := record.policy_observation()) is not None
    )
    if not policy_observations:
        last = reconciled[-1]
        reconciled.append(
            InterpretedObservation(
                sequence=last.sequence,
                observed_at=last.observed_at,
                stage=last.stage,
                channel=ObservationChannel.RECONCILIATION,
                target=last.target,
                attempt=last.attempt,
                signal="insufficient_evidence",
                kind=EvidenceKind.USER_OUTAGE,
            )
        )
        policy_observations = (reconciled[-1].policy_observation(),)
    evidence = AvailabilityEvidence(
        source_authority=source_authority,
        user_state=user_state,
        observations=tuple(item for item in policy_observations if item is not None),
        investigation_stages=investigation_stages,
    )
    return AvailabilityEvaluation(
        records=tuple(reconciled),
        evidence=evidence,
        decision=decide_availability(evidence),
    )


def evaluate_availability(
    *,
    source_authority: SourceAuthority,
    user_state: UserState,
    observations: tuple[RawObservation, ...],
    investigation_stages: tuple[str, ...] = (),
    previous_records: tuple[InterpretedObservation, ...] = (),
) -> AvailabilityEvaluation:
    """Interpret one ordered observation history and apply approved policy."""

    if not observations:
        raise ValueError("availability evaluation requires at least one observation")
    sequences = [observation.sequence for observation in observations]
    if any(current <= previous for previous, current in zip(sequences, sequences[1:])):
        raise ValueError("observation sequence must be strictly increasing")

    retained = [
        record
        for record in previous_records
        if record.channel is not ObservationChannel.RECONCILIATION
    ]
    if retained and sequences[0] <= max(record.sequence for record in retained):
        raise ValueError("new observation sequence must follow previous evidence")

    records: list[InterpretedObservation] = retained
    for observation in observations:
        if isinstance(observation, NetworkObservation):
            records.append(interpret_network(observation))
        elif isinstance(observation, SourceObservation):
            records.append(interpret_source(observation))
        else:
            records.append(interpret_investigation(observation))
    return evaluate_interpreted_availability(
        source_authority=source_authority,
        user_state=user_state,
        records=tuple(records),
        investigation_stages=investigation_stages,
    )
