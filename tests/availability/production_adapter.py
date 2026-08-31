"""Translate the approved corpus boundary into production availability types."""

from __future__ import annotations

import json
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from internshelper import db, store
from internshelper.availability import (
    AvailabilityDecision,
    AvailabilityEvidence,
    EvidenceKind,
    EvidenceObservation,
    SourceAuthority,
    UserState,
    decide_availability,
)
from internshelper.availability_checks import (
    CheckStage,
    InvestigationFinding,
    InvestigationObservation,
    NetworkFailure,
    NetworkObservation,
    RawObservation,
    SourceObservation,
    evaluate_availability,
)
from internshelper.availability_store import (
    ensure_pending,
    get_projection,
    persist_evaluation,
)
from internshelper.availability_service import (
    derive_investigation_stages,
    verify_posting,
)
from internshelper.models import Posting

from .fakes import EvidenceEvent, Scenario, build_scenario


FIXTURES = Path(__file__).parent / "fixtures"
ORIGINAL_URL = "https://jobs.example.test/roles/original"
_OBSERVATION_EPOCH = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


_AUTOMATION_SIGNALS = {"http_403", "anti_bot", "javascript_required", "login_required"}
_OUTAGE_SIGNALS = {
    "timeout",
    "dns_failure",
    "connection_failure",
    "http_429",
    "http_5xx",
    "maintenance",
    "redirect_loop",
    "generic_error_text",
}
_IGNORED_SUPPORTING_SIGNALS = {
    "redirect",
    "unusual_title",
    "replacement_url",
    "multiple_source_observations",
}
_DIRECT_SIGNAL_KINDS = {
    "live_page": EvidenceKind.DESTINATION_LIVE,
    "single_404": EvidenceKind.DESTINATION_NOT_FOUND,
    "repeated_404": EvidenceKind.DESTINATION_NOT_FOUND,
    "single_410": EvidenceKind.DESTINATION_GONE,
    "repeated_410": EvidenceKind.DESTINATION_GONE,
    "employer_closure_text": EvidenceKind.EMPLOYER_CLOSED,
    "single_ats_absence": EvidenceKind.FIRST_PARTY_ABSENT,
    "repeated_ats_absence": EvidenceKind.FIRST_PARTY_ABSENT,
    "community_presence": EvidenceKind.COMMUNITY_PRESENT,
    "community_removal": EvidenceKind.COMMUNITY_REMOVED,
    "conflicting_evidence": EvidenceKind.TRUSTWORTHY_CONFLICT,
    "original_recovered": EvidenceKind.ORIGINAL_RECOVERED,
    "closure_confirmed": EvidenceKind.CLOSURE_CONFIRMED,
    "investigation_unresolved": EvidenceKind.INVESTIGATION_UNRESOLVED,
}
_KNOWN_SIGNALS = (
    set(_DIRECT_SIGNAL_KINDS)
    | _AUTOMATION_SIGNALS
    | _OUTAGE_SIGNALS
    | _IGNORED_SUPPORTING_SIGNALS
    | {"replacement_discovered"}
)


def _observations(case: dict[str, Any]) -> tuple[EvidenceObservation, ...]:
    observations: list[EvidenceObservation] = []
    for event in case["evidence_timeline"]:
        signals = set(event["signals"])
        unknown = signals - _KNOWN_SIGNALS
        if unknown:
            raise ValueError(f"unmapped availability signals: {', '.join(sorted(unknown))}")

        for signal in sorted(signals & set(_DIRECT_SIGNAL_KINDS)):
            observations.append(
                EvidenceObservation(
                    _DIRECT_SIGNAL_KINDS[signal],
                    attempt=event["attempt"],
                    subject=event["target"],
                )
            )
        if signals & _AUTOMATION_SIGNALS:
            observations.append(
                EvidenceObservation(
                    EvidenceKind.AUTOMATION_BARRIER,
                    event["attempt"],
                    subject=event["target"],
                )
            )
        if signals & _OUTAGE_SIGNALS:
            observations.append(
                EvidenceObservation(
                    EvidenceKind.USER_OUTAGE,
                    event["attempt"],
                    subject=event["target"],
                )
            )
        if "replacement_discovered" in signals:
            observations.append(
                EvidenceObservation(
                    EvidenceKind.REPLACEMENT_FOUND,
                    event["attempt"],
                    candidate_url=event.get("candidate_url"),
                    subject=event["target"],
                )
            )
    return tuple(observations)


def _result(decision: AvailabilityDecision) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in fields(decision):
        value = getattr(decision, field.name)
        if isinstance(value, Enum):
            value = value.value
        elif isinstance(value, tuple):
            value = list(value)
        result[field.name] = value
    return result


def _fixture_text(event: EvidenceEvent) -> str:
    return (FIXTURES / event.fixture).read_text(encoding="utf-8")


def _observed_at(event: EvidenceEvent) -> str:
    return (_OBSERVATION_EPOCH + timedelta(minutes=event.sequence)).isoformat()


def _candidate_url(value: Any) -> str | None:
    """Find the replacement-shaped URL in a sanitized source fixture."""

    if isinstance(value, dict):
        for key in ("candidate", "second_observation", "postings"):
            if key in value:
                found = _candidate_url(value[key])
                if found:
                    return found
        direct = value.get("url")
        if isinstance(direct, str) and direct.startswith(("http://", "https://")):
            return direct
        for nested in value.values():
            found = _candidate_url(nested)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _candidate_url(nested)
            if found:
                return found
    return None


def _source_authority(case: dict[str, Any], event: EvidenceEvent) -> SourceAuthority:
    if event.target.startswith("community_row"):
        return SourceAuthority.COMMUNITY_LIST
    if event.target.startswith("first_party") or event.target == "original_posting_id":
        return SourceAuthority.FIRST_PARTY_ATS
    if event.target == "replacement_candidate":
        return SourceAuthority.FIRST_PARTY_ATS
    if event.target == "unfamiliar_source":
        return SourceAuthority.UNKNOWN
    return SourceAuthority(case["source"]["authority"])


def _network_observation(
    case: dict[str, Any], event: EvidenceEvent
) -> NetworkObservation:
    failures = {
        "timeout": NetworkFailure.TIMEOUT,
        "dns_error": NetworkFailure.DNS,
        "connection_error": NetworkFailure.CONNECTION,
        "redirect_loop": NetworkFailure.REDIRECT_LOOP,
    }
    failure = failures.get(event.result)
    employer_hosted = (
        case["source"]["authority"] == SourceAuthority.FIRST_PARTY_ATS.value
        or event.result == "employer_closure"
        or event.target in {"redirected_url", "employer_board_b"}
    )
    return NetworkObservation(
        sequence=event.sequence,
        observed_at=_observed_at(event),
        stage=CheckStage(event.stage),
        target=event.target,
        attempt=event.attempt,
        status=None if failure else event.status,
        body="" if failure else _fixture_text(event),
        location=event.location,
        failure=failure,
        employer_hosted=employer_hosted,
        checker_only=event.result == "forbidden_to_checker",
    )


def _source_observation(
    case: dict[str, Any], event: EvidenceEvent
) -> SourceObservation:
    authority = _source_authority(case, event)
    raw_fixture = _fixture_text(event)
    is_candidate = event.target in {"replacement_candidate", "community_row_b"}
    candidate = None
    error = None
    status = event.status
    if event.fixture.endswith(".json"):
        fixture = json.loads(raw_fixture)
        if "complete" in fixture:
            complete = bool(fixture.get("complete"))
            postings = fixture.get("postings") or []
            original_present = (
                any(
                    item.get("id") == "role-example"
                    or item.get("url") == ORIGINAL_URL
                    for item in postings
                )
                if complete
                else None
            )
            error = fixture.get("error") if not complete else None
        elif event.target == "first_party_board" and "cxs_enumeration" in fixture:
            source = fixture["cxs_enumeration"]
            complete = bool(source.get("complete"))
            original_present = False if complete else None
            status = source.get("status")
            error = None if complete else "first-party enumeration failed"
        else:
            complete = True
            original_present = True
        if is_candidate:
            candidate = _candidate_url(fixture)
            if candidate is not None:
                original_present = True
    else:
        complete = True
        original_present = ORIGINAL_URL in raw_fixture
    return SourceObservation(
        sequence=event.sequence,
        observed_at=_observed_at(event),
        stage=CheckStage(event.stage),
        target=event.target,
        attempt=event.attempt,
        authority=authority,
        complete=complete,
        original_present=original_present,
        candidate_url=candidate,
        status=status,
        error=error,
    )


def _investigation_observation(event: EvidenceEvent) -> InvestigationObservation:
    return InvestigationObservation(
        sequence=event.sequence,
        observed_at=_observed_at(event),
        stage=CheckStage(event.stage),
        target=event.target,
        attempt=event.attempt,
        finding=InvestigationFinding(event.result),
        candidate_url=event.candidate_url,
    )


def _scripted_observations(
    case: dict[str, Any], scenario: Scenario
) -> tuple[RawObservation, ...]:
    """Consume fixture collaborators in global chronology without using signals."""

    observations: list[RawObservation] = []
    for raw_event in case["evidence_timeline"]:
        channel = raw_event["channel"]
        target = raw_event["target"]
        if channel == "network":
            event = scenario.network.request(target)
            observations.append(_network_observation(case, event))
        elif channel == "source":
            event = scenario.sources.enumerate(target)
            observations.append(_source_observation(case, event))
        elif channel == "investigator":
            event = scenario.investigator.finding(target)
            observations.append(_investigation_observation(event))
        else:  # corpus validation should make this unreachable
            raise ValueError(f"unsupported scripted channel: {channel}")
    return tuple(observations)


def _scripted_acquired_observations(
    case: dict[str, Any], scenario: Scenario
) -> tuple[RawObservation, ...]:
    """Consume only evidence collaborators; production derives the final finding."""

    observations: list[RawObservation] = []
    for raw_event in case["evidence_timeline"]:
        channel = raw_event["channel"]
        target = raw_event["target"]
        if channel == "network":
            observations.append(
                _network_observation(case, scenario.network.request(target))
            )
        elif channel == "source":
            observations.append(
                _source_observation(case, scenario.sources.enumerate(target))
            )
    return tuple(observations)


def _semantic_stages(
    authority: SourceAuthority, observations: tuple[RawObservation, ...]
) -> tuple[str, ...]:
    if not any(
        isinstance(item, InvestigationObservation) for item in observations
    ):
        return ()
    return tuple(
        stage.value
        for stage in derive_investigation_stages(
            authority=authority,
            observations=observations,
            previous=(),
        )
    )


class _UnusedChecker:
    def check(self, url: str, **kwargs):  # pragma: no cover - provider owns acquisition
        raise AssertionError("contract evidence provider unexpectedly called HTTP checker")


class _ScenarioEvidenceProvider:
    def __init__(self, case: dict[str, Any], scenario: Scenario):
        self.case = case
        self.scenario = scenario

    def gather(self, conn, original, **kwargs):
        return _scripted_acquired_observations(self.case, self.scenario)


def _contract_source_key(case: dict[str, Any]) -> str:
    authority = SourceAuthority(case["source"]["authority"])
    if authority is SourceAuthority.COMMUNITY_LIST:
        return "markdown:contract"
    if authority is SourceAuthority.FIRST_PARTY_ATS:
        return "greenhouse:contract"
    return "unfamiliar:contract"


class ProductionAvailabilityAdapter:
    """Translate frozen collaborators into the production availability boundaries."""

    def decide(self, case: dict[str, Any]) -> dict[str, Any]:
        scenario = build_scenario(case)
        raw_observations = _scripted_observations(case, scenario)
        authority = SourceAuthority(case["source"]["authority"])
        evidence = AvailabilityEvidence(
            source_authority=authority,
            user_state=UserState(case["user_state"]),
            observations=_observations(case),
            investigation_stages=_semantic_stages(authority, raw_observations),
        )
        return _result(decide_availability(evidence))

    def interpret(self, case: dict[str, Any], scenario: Scenario) -> dict[str, Any]:
        authority = SourceAuthority(case["source"]["authority"])
        observations = _scripted_observations(case, scenario)
        evaluation = evaluate_availability(
            source_authority=authority,
            user_state=UserState(case["user_state"]),
            observations=observations,
            investigation_stages=_semantic_stages(authority, observations),
        )
        return _result(evaluation.decision)

    def collect_to_board(
        self, case: dict[str, Any], scenario: Scenario, db_path: Path
    ) -> dict[str, Any]:
        conn = db.connect(db_path)
        posting_id = f"availability-contract:{case['id']}"
        try:
            db.init_db(conn)
            posting = Posting(
                posting_id=posting_id,
                source_key=f"{case['source']['kind']}:contract",
                title="Software Engineering Intern",
                company="Availability Contract Employer",
                location="Example City",
                url=ORIGINAL_URL,
                is_internship=True,
                is_cs_relevant=True,
            )
            store.upsert(conn, posting, now=_OBSERVATION_EPOCH.isoformat())
            conn.execute(
                "UPDATE postings SET rank_score = ? WHERE posting_id = ?",
                (73.5, posting_id),
            )
            conn.commit()
            if case["user_state"] == UserState.INTERESTED.value:
                store.set_application_status(conn, posting_id, "Interested")
            elif case["user_state"] == UserState.APPLIED.value:
                store.set_application_status(conn, posting_id, "Applied")

            authority = SourceAuthority(case["source"]["authority"])
            ensure_pending(
                conn,
                posting_id,
                authority,
                now=_OBSERVATION_EPOCH.isoformat(),
            )
            observations = _scripted_observations(case, scenario)
            evaluation = evaluate_availability(
                source_authority=authority,
                user_state=UserState(case["user_state"]),
                observations=observations,
                investigation_stages=_semantic_stages(authority, observations),
            )
            persist_evaluation(
                conn,
                posting_id,
                evaluation,
                updated_at=evaluation.records[-1].observed_at,
            )
            projection = get_projection(conn, posting_id)
            visible_ids = {row["posting_id"] for row in store.inbox_with_status(conn)}
            if projection.visible != (posting_id in visible_ids):
                raise AssertionError("Board visibility disagrees with availability projection")
            stored = conn.execute(
                "SELECT url, rank_score FROM postings WHERE posting_id = ?",
                (posting_id,),
            ).fetchone()
            if stored["url"] != ORIGINAL_URL or stored["rank_score"] != 73.5:
                raise AssertionError("availability changed the stored URL or rank score")
            return _result(projection.decision)
        finally:
            conn.close()

    def investigate(self, case: dict[str, Any], scenario: Scenario) -> dict[str, Any]:
        conn = db.connect(":memory:")
        posting_id = f"availability-investigation:{case['id']}"
        try:
            db.init_db(conn)
            store.upsert(
                conn,
                Posting(
                    posting_id=posting_id,
                    source_key=_contract_source_key(case),
                    title="Software Engineering Intern",
                    company="Availability Contract Employer",
                    location="Example City",
                    url=ORIGINAL_URL,
                    is_internship=True,
                    is_cs_relevant=True,
                ),
                now=_OBSERVATION_EPOCH.isoformat(),
            )
            if case["user_state"] == UserState.INTERESTED.value:
                store.set_application_status(conn, posting_id, "Interested")
            elif case["user_state"] == UserState.APPLIED.value:
                store.set_application_status(conn, posting_id, "Applied")
            result = verify_posting(
                conn,
                posting_id,
                sources_path="",
                now=_OBSERVATION_EPOCH.isoformat(),
                checker=_UnusedChecker(),
                evidence_provider=_ScenarioEvidenceProvider(case, scenario),
            )
            return _result(result.report.evaluation.decision)
        finally:
            conn.close()


def create_adapter() -> ProductionAvailabilityAdapter:
    return ProductionAvailabilityAdapter()
