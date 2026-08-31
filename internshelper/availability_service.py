"""Persistence-aware user-triggered availability investigation service."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Protocol

from internshelper import config
from internshelper.availability import AvailabilityStatus, EvidenceKind, SourceAuthority
from internshelper.availability_checks import (
    CheckStage,
    InvestigationFinding,
    InvestigationObservation,
    InterpretedObservation,
    NetworkObservation,
    ObservationChannel,
    RawObservation,
    SourceObservation,
    evaluate_availability,
    interpret_network,
)
from internshelper.availability_investigation import (
    InvestigationReport,
    InvestigationStage,
    conclude_investigation,
)
from internshelper.availability_runtime import HttpDestinationChecker
from internshelper.availability_store import (
    AvailabilityProjection,
    ensure_pending,
    get_projection,
    get_state,
    load_current_records,
    persist_evaluation,
    user_state_for_posting,
)
from internshelper.connectors import build_connector


class DestinationChecker(Protocol):
    def check(self, url: str, **kwargs) -> tuple[NetworkObservation, ...]: ...


class InvestigationEvidenceProvider(Protocol):
    """Injectable acquisition boundary; the service still owns policy and progress."""

    def gather(
        self,
        conn: sqlite3.Connection,
        original: sqlite3.Row,
        *,
        sources_path: str,
        now: str,
        checker: DestinationChecker,
        authority: SourceAuthority,
        sequence: int,
        attempt: int,
    ) -> tuple[RawObservation, ...]: ...


@dataclass(frozen=True)
class VerificationResult:
    report: InvestigationReport
    projection: AvailabilityProjection


@dataclass(frozen=True)
class _ReplacementCandidate:
    """A candidate URL paired with the authority that actually supplied it."""

    title: str
    company: str
    url: str
    authority: SourceAuthority


def _authority_for_source_key(source_key: str) -> SourceAuthority:
    source_type = source_key.split(":", 1)[0]
    if source_type in {"github", "markdown"}:
        return SourceAuthority.COMMUNITY_LIST
    if source_type in {"greenhouse", "lever", "ashby", "workday", "amazon"}:
        return SourceAuthority.FIRST_PARTY_ATS
    return SourceAuthority.UNKNOWN


def _tokens(value: str | None) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", (value or "").lower())
        if token not in {"intern", "internship", "the", "and"}
    }


def _candidate_score(original, candidate: _ReplacementCandidate) -> float:
    if not candidate.url or candidate.url == original["effective_url"]:
        return 0.0
    left = _tokens(original["title"])
    right = _tokens(candidate.title)
    if not left or not right:
        return 0.0
    overlap = len(left & right) / len(left | right)
    original_company = " ".join(sorted(_tokens(original["company"])))
    candidate_company = " ".join(sorted(_tokens(candidate.company)))
    if original_company and candidate_company and original_company != candidate_company:
        overlap *= 0.5
    return overlap


def _best_candidate(original, candidates: list[_ReplacementCandidate]):
    scored = sorted(
        ((_candidate_score(original, item), item) for item in candidates),
        key=lambda pair: pair[0],
        reverse=True,
    )
    return scored[0][1] if scored and scored[0][0] >= 0.6 else None


def _database_candidates(
    conn: sqlite3.Connection, original
) -> list[_ReplacementCandidate]:
    if not _tokens(original["company"]):
        return []
    rows = conn.execute(
        """
        SELECT p.*, av.confirmed_url, av.confirmed_url_authority,
               COALESCE(av.confirmed_url, p.url) AS effective_url
        FROM postings p
        LEFT JOIN availability_state av ON av.posting_id = p.posting_id
        WHERE p.posting_id != ?
          AND LOWER(COALESCE(p.company, '')) = LOWER(?)
          AND (p.verdict IS NULL OR p.verdict != 'no_match')
          AND p.flagged_at IS NULL AND p.duplicate_of IS NULL
          AND p.is_active = 1
          AND (av.status IS NULL OR av.status != 'closed')
        """,
        (original["posting_id"], original["company"] or ""),
    ).fetchall()
    return [
        _ReplacementCandidate(
            title=row["title"],
            company=row["company"],
            url=row["effective_url"],
            authority=(
                SourceAuthority(
                    row["confirmed_url_authority"] or SourceAuthority.UNKNOWN.value
                )
                if row["confirmed_url"]
                else _authority_for_source_key(row["source_key"])
            ),
        )
        for row in rows
    ]


def _load_sources(sources_path: str):
    try:
        entries, _errors = config.load_sources(sources_path)
    except Exception:
        return []
    return entries


class RuntimeEvidenceProvider:
    """Perform bounded live reads only for an explicit user verification action."""

    def gather(
        self,
        conn: sqlite3.Connection,
        original: sqlite3.Row,
        *,
        sources_path: str,
        now: str,
        checker: DestinationChecker,
        authority: SourceAuthority,
        sequence: int,
        attempt: int,
    ) -> tuple[RawObservation, ...]:
        observations: list[RawObservation] = []
        target = original["confirmed_url"] or "original_url"
        employer_hosted = authority is SourceAuthority.FIRST_PARTY_ATS

        def check_original(current_attempt: int) -> set[EvidenceKind]:
            checked = checker.check(
                original["effective_url"],
                sequence=sequence + len(observations),
                attempt=current_attempt,
                stage=CheckStage.USER_INVESTIGATION,
                observed_at=now,
                employer_hosted=employer_hosted,
                target=target,
            )
            if not checked:
                raise RuntimeError("availability checker returned no observations")
            observations.extend(checked)
            return {
                record.kind
                for item in checked
                if (record := interpret_network(item)).kind is not None
            }

        kinds = check_original(attempt)
        if kinds & {EvidenceKind.DESTINATION_LIVE, EvidenceKind.EMPLOYER_CLOSED}:
            return tuple(observations)
        kinds |= check_original(attempt + 1)
        if kinds & {EvidenceKind.DESTINATION_LIVE, EvidenceKind.EMPLOYER_CLOSED}:
            return tuple(observations)

        entries = _load_sources(sources_path)
        if original["source_key"]:
            entries.sort(key=lambda item: item.source_key != original["source_key"])
        source_candidates: list[_ReplacementCandidate] = []
        if not entries:
            observations.append(
                SourceObservation(
                    sequence=sequence + len(observations),
                    observed_at=now,
                    stage=CheckStage.USER_INVESTIGATION,
                    target="configured_sources",
                    attempt=attempt,
                    authority=authority,
                    complete=False,
                    original_present=None,
                    error="no relevant configured source could be enumerated",
                )
            )
        for entry in entries:
            entry_authority = _authority_for_source_key(entry.source_key)
            current_sequence = sequence + len(observations)
            try:
                connector = build_connector(entry)
                result = connector.fetch()
                fetched = result.postings
                present = any(
                    item.posting_id == original["posting_id"]
                    or item.url in {original["url"], original["effective_url"]}
                    for item in fetched
                )
                observations.append(
                    SourceObservation(
                        sequence=current_sequence,
                        observed_at=now,
                        stage=CheckStage.USER_INVESTIGATION,
                        target=entry.source_key,
                        attempt=attempt,
                        authority=entry_authority,
                        complete=result.complete,
                        original_present=present if result.complete else None,
                        error=(
                            None
                            if result.complete
                            else "partial enumeration: "
                            + (
                                "; ".join(connector.diagnostics)
                                or "connector could not prove completeness"
                            )
                        ),
                    )
                )
                source_candidates.extend(
                    _ReplacementCandidate(
                        title=item.title,
                        company=item.company,
                        url=item.url,
                        authority=entry_authority,
                    )
                    for item in fetched
                )
            except Exception as exc:
                observations.append(
                    SourceObservation(
                        sequence=current_sequence,
                        observed_at=now,
                        stage=CheckStage.USER_INVESTIGATION,
                        target=entry.source_key,
                        attempt=attempt,
                        authority=entry_authority,
                        complete=False,
                        original_present=None,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )

        candidate = _best_candidate(original, source_candidates)
        if candidate is None:
            candidate = _best_candidate(original, _database_candidates(conn, original))
        if candidate is not None:
            observations.append(
                SourceObservation(
                    sequence=sequence + len(observations),
                    observed_at=now,
                    stage=CheckStage.USER_INVESTIGATION,
                    target="replacement_candidate",
                    attempt=attempt,
                    authority=candidate.authority,
                    complete=True,
                    original_present=True,
                    candidate_url=candidate.url,
                )
            )
        return tuple(observations)


def _candidate_url(
    observations: tuple[RawObservation, ...],
    previous: tuple[InterpretedObservation, ...],
) -> str | None:
    for item in observations:
        if isinstance(item, (SourceObservation, InvestigationObservation)) and item.candidate_url:
            return item.candidate_url
    return next((item.candidate_url for item in previous if item.candidate_url), None)


def derive_investigation_stages(
    *,
    authority: SourceAuthority,
    observations: tuple[RawObservation, ...],
    previous: tuple[InterpretedObservation, ...],
    candidate_url: str | None = None,
) -> tuple[InvestigationStage, ...]:
    candidate_url = candidate_url or _candidate_url(observations, previous)
    stages = [InvestigationStage.QUEUED, InvestigationStage.CHECK_ORIGINAL]
    network_attempts = {
        item.attempt for item in observations if isinstance(item, NetworkObservation)
    } | {
        item.attempt
        for item in previous
        if item.channel is ObservationChannel.NETWORK
    }
    source_items = [item for item in observations if isinstance(item, SourceObservation)]
    source_authorities = {item.authority for item in source_items} | {
        item.authority
        for item in previous
        if item.channel is ObservationChannel.SOURCE and item.authority is not None
    }
    has_first_party = SourceAuthority.FIRST_PARTY_ATS in source_authorities
    has_comparison = bool(source_items) and (
        authority is not SourceAuthority.FIRST_PARTY_ATS
        or any(item is not SourceAuthority.FIRST_PARTY_ATS for item in source_authorities)
    )
    retried = len(network_attempts) >= 2
    implicit_retry_after_source = bool(source_items) and not retried and not any(
        isinstance(item, NetworkObservation)
        and item.stage is CheckStage.USER_INVESTIGATION
        for item in observations
    )

    if retried:
        stages.append(InvestigationStage.RETRY_ORIGINAL)
    if has_first_party:
        stages.append(InvestigationStage.ENUMERATE_FIRST_PARTY)
    if has_comparison:
        stages.append(InvestigationStage.COMPARE_SOURCES)
    if implicit_retry_after_source:
        stages.append(InvestigationStage.RETRY_ORIGINAL)

    if candidate_url:
        if not (authority is SourceAuthority.COMMUNITY_LIST and has_first_party):
            stages.append(InvestigationStage.SEARCH_REPLACEMENT)
        stages.append(InvestigationStage.REVIEW_FINDING)
        stages.append(InvestigationStage.AWAIT_USER_CONFIRMATION)
    else:
        stages.append(InvestigationStage.COMPLETE)
    return tuple(stages)


def _without_previous_outcome(
    records: tuple[InterpretedObservation, ...],
) -> tuple[InterpretedObservation, ...]:
    return tuple(
        item
        for item in records
        if item.channel not in {
            ObservationChannel.INVESTIGATOR,
            ObservationChannel.RECONCILIATION,
        }
    )


def _records_for_target(
    records: tuple[InterpretedObservation, ...], target: str
) -> tuple[InterpretedObservation, ...]:
    """Keep evidence from one checked URL and only the redirects it recorded."""

    related_targets = {target}
    selected: list[InterpretedObservation] = []
    for record in records:
        if record.target not in related_targets:
            continue
        selected.append(record)
        if record.location:
            related_targets.add(record.location)
    return tuple(selected)


def verify_posting(
    conn: sqlite3.Connection,
    posting_id: str,
    *,
    sources_path: str,
    now: str,
    checker: DestinationChecker | None = None,
    evidence_provider: InvestigationEvidenceProvider | None = None,
) -> VerificationResult:
    """Investigate one posting while preserving retry identity and semantic progress."""

    original = conn.execute(
        """
        SELECT p.*, av.confirmed_url, av.confirmed_url_authority,
               COALESCE(av.confirmed_url, p.url) AS effective_url
        FROM postings p
        LEFT JOIN availability_state av ON av.posting_id = p.posting_id
        WHERE p.posting_id = ?
        """,
        (posting_id,),
    ).fetchone()
    if original is None:
        raise LookupError(f"unknown posting: {posting_id}")
    if get_state(conn, posting_id) is None:
        ensure_pending(
            conn,
            posting_id,
            _authority_for_source_key(original["source_key"]),
            now=now,
        )

    current = load_current_records(conn, posting_id)
    previous = _without_previous_outcome(current)
    state = get_state(conn, posting_id)
    assert state is not None
    authority = SourceAuthority(state["source_authority"])
    if state["confirmed_url"]:
        authority = SourceAuthority(
            state["confirmed_url_authority"] or SourceAuthority.UNKNOWN.value
        )
        previous = _records_for_target(previous, state["confirmed_url"])
    sequence = max((item.sequence for item in current), default=0) + 1
    attempt = int(state["last_attempt"] or 0) + 1

    owned_checker = checker is None
    checker = checker or HttpDestinationChecker()
    provider = evidence_provider or RuntimeEvidenceProvider()
    try:
        observations = provider.gather(
            conn,
            original,
            sources_path=sources_path,
            now=now,
            checker=checker,
            authority=authority,
            sequence=sequence,
            attempt=attempt,
        )
        if not observations:
            raise RuntimeError("availability investigation produced no observations")
        preliminary = evaluate_availability(
            source_authority=authority,
            user_state=user_state_for_posting(conn, posting_id),
            observations=observations,
            previous_records=previous,
        )
        candidate_url = _candidate_url(observations, previous)
        if candidate_url:
            finding = InvestigationFinding.REPLACEMENT_FOUND
        elif preliminary.decision.availability is AvailabilityStatus.CLOSED:
            finding = InvestigationFinding.CLOSURE_CONFIRMED
        elif preliminary.decision.availability is AvailabilityStatus.LIVE:
            finding = InvestigationFinding.ORIGINAL_RECOVERED
        else:
            finding = InvestigationFinding.UNRESOLVED
        stages = derive_investigation_stages(
            authority=authority,
            observations=observations,
            previous=previous,
            candidate_url=candidate_url,
        )
        final_sequence = max(
            [item.sequence for item in observations]
            + [item.sequence for item in previous]
        ) + 1
        final_attempt = max(
            [item.attempt for item in observations]
            + [item.attempt for item in previous]
        )
        observations_with_finding = (
            *observations,
            InvestigationObservation(
                sequence=final_sequence,
                observed_at=now,
                stage=CheckStage.USER_INVESTIGATION,
                target=(
                    "replacement_candidate"
                    if candidate_url
                    else original["effective_url"]
                    if original["confirmed_url"]
                    else "original_url"
                ),
                attempt=final_attempt,
                finding=finding,
                candidate_url=candidate_url,
            ),
        )
        report = conclude_investigation(
            source_authority=authority,
            user_state=user_state_for_posting(conn, posting_id),
            observations=observations_with_finding,
            stages=stages,
            previous_records=previous,
        )
        persist_evaluation(conn, posting_id, report.evaluation, updated_at=now)
        return VerificationResult(
            report=report,
            projection=get_projection(conn, posting_id),
        )
    finally:
        if owned_checker and isinstance(checker, HttpDestinationChecker):
            checker.close()
