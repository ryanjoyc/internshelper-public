"""SQLite persistence and Board projection for posting availability.

Availability archival is a view decision.  This module never writes curation
verdicts, ranking fields, application state, or the connector-owned posting URL.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, replace
from typing import Iterable

from internshelper.availability import (
    AvailabilityDecision,
    AvailabilityEvidence,
    AvailabilityStatus,
    BoardTreatment,
    EvidenceKind,
    EvidenceObservation,
    SourceAuthority,
    UserState,
    decide_availability,
)
from internshelper.availability_checks import (
    AvailabilityEvaluation,
    CheckStage,
    InterpretedObservation,
    ObservationChannel,
    evaluate_interpreted_availability,
)


PIPELINE_STATUSES = {"Applied", "Interviewing", "Offer", "Rejected"}


@dataclass(frozen=True)
class AvailabilityProjection:
    posting_id: str
    decision: AvailabilityDecision
    visible: bool
    original_url: str | None
    effective_url: str | None
    validation_completed: bool
    confirmed_url: str | None


def ensure_pending(
    conn: sqlite3.Connection,
    posting_id: str,
    source_authority: SourceAuthority,
    *,
    now: str,
) -> None:
    """Create an initial hidden state without disturbing an existing assessment."""

    if conn.execute(
        "SELECT 1 FROM postings WHERE posting_id = ?", (posting_id,)
    ).fetchone() is None:
        raise LookupError(f"unknown posting: {posting_id}")
    conn.execute(
        """
        INSERT INTO availability_state (
            posting_id, source_authority, status, validation_completed, updated_at
        ) VALUES (?, ?, NULL, 0, ?)
        ON CONFLICT(posting_id) DO NOTHING
        """,
        (posting_id, source_authority.value, now),
    )
    conn.commit()


def _record_key(record: InterpretedObservation) -> str:
    raw = json.dumps(
        [
            record.sequence,
            record.observed_at,
            record.stage.value,
            record.channel.value,
            record.target,
            record.attempt,
            record.signal,
            record.kind.value if record.kind else None,
            record.status,
            record.location,
            record.candidate_url,
            record.authority.value if record.authority else None,
            record.trustworthy,
            record.detail,
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _candidate_authority(
    records: tuple[InterpretedObservation, ...], candidate_url: str | None
) -> str | None:
    if candidate_url is None:
        return None
    return next(
        (
            record.authority.value
            for record in reversed(records)
            if record.candidate_url == candidate_url and record.authority is not None
        ),
        None,
    )


def persist_evaluation(
    conn: sqlite3.Connection,
    posting_id: str,
    evaluation: AvailabilityEvaluation,
    *,
    updated_at: str,
    next_check_at: str | None = None,
) -> None:
    """Atomically replace the current decision window and append its history."""

    if conn.execute(
        "SELECT 1 FROM postings WHERE posting_id = ?", (posting_id,)
    ).fetchone() is None:
        raise LookupError(f"unknown posting: {posting_id}")
    existing = get_state(conn, posting_id)
    confirmed_url = existing["confirmed_url"] if existing else None
    existing_candidate = existing["pending_candidate_url"] if existing else None
    existing_candidate_authority = (
        existing["pending_candidate_authority"] if existing else None
    )
    candidate = evaluation.decision.replacement_candidate_url
    retained_candidate = bool(
        candidate is None
        and existing_candidate
        and evaluation.decision.availability is not AvailabilityStatus.LIVE
    )
    if candidate is not None:
        pending_candidate = None if candidate == confirmed_url else candidate
        pending_candidate_authority = (
            None
            if pending_candidate is None
            else _candidate_authority(evaluation.records, candidate)
        )
    elif retained_candidate:
        pending_candidate = existing_candidate
        pending_candidate_authority = existing_candidate_authority
    else:
        pending_candidate = None
        pending_candidate_authority = None
    records = evaluation.records
    last_checked_at = records[-1].observed_at
    last_attempt = max(record.attempt for record in records)
    stages = (
        existing["investigation_stages"]
        if retained_candidate
        else json.dumps(list(evaluation.decision.investigation_stages))
    )
    investigation_outcome = (
        existing["investigation_outcome"]
        if retained_candidate
        else evaluation.decision.investigation_outcome.value
    )

    try:
        conn.execute(
            """
            INSERT INTO availability_state (
                posting_id, source_authority, status, validation_completed,
                last_attempt, last_checked_at, next_check_at,
                pending_candidate_url, pending_candidate_authority, investigation_outcome,
                investigation_stages, updated_at
            ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(posting_id) DO UPDATE SET
                status = excluded.status,
                validation_completed = 1,
                last_attempt = excluded.last_attempt,
                last_checked_at = excluded.last_checked_at,
                next_check_at = excluded.next_check_at,
                pending_candidate_url = excluded.pending_candidate_url,
                pending_candidate_authority = excluded.pending_candidate_authority,
                investigation_outcome = excluded.investigation_outcome,
                investigation_stages = excluded.investigation_stages,
                updated_at = excluded.updated_at
            """,
            (
                posting_id,
                evaluation.evidence.source_authority.value,
                evaluation.decision.availability.value,
                last_attempt,
                last_checked_at,
                next_check_at,
                pending_candidate,
                pending_candidate_authority,
                investigation_outcome,
                stages,
                updated_at,
            ),
        )
        conn.execute(
            "UPDATE availability_evidence SET is_current = 0 WHERE posting_id = ?",
            (posting_id,),
        )
        for record in records:
            conn.execute(
                """
                INSERT INTO availability_evidence (
                    posting_id, record_key, sequence, observed_at, stage, channel,
                    target, attempt, signal, kind, status, location, candidate_url,
                    authority, trustworthy, detail, is_current
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(posting_id, record_key) DO UPDATE SET is_current = 1
                """,
                (
                    posting_id,
                    _record_key(record),
                    record.sequence,
                    record.observed_at,
                    record.stage.value,
                    record.channel.value,
                    record.target,
                    record.attempt,
                    record.signal,
                    record.kind.value if record.kind else None,
                    record.status,
                    record.location,
                    record.candidate_url,
                    record.authority.value if record.authority else None,
                    int(record.trustworthy),
                    record.detail,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_state(conn: sqlite3.Connection, posting_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM availability_state WHERE posting_id = ?", (posting_id,)
    ).fetchone()


def evidence_history(
    conn: sqlite3.Connection, posting_id: str
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM availability_evidence WHERE posting_id = ? ORDER BY id",
        (posting_id,),
    ).fetchall()


def load_current_records(
    conn: sqlite3.Connection, posting_id: str
) -> tuple[InterpretedObservation, ...]:
    """Rehydrate the current normalized window for a scheduled retry."""

    rows = conn.execute(
        "SELECT * FROM availability_evidence "
        "WHERE posting_id = ? AND is_current = 1 ORDER BY id",
        (posting_id,),
    ).fetchall()
    return tuple(
        InterpretedObservation(
            sequence=row["sequence"],
            observed_at=row["observed_at"],
            stage=CheckStage(row["stage"]),
            channel=ObservationChannel(row["channel"]),
            target=row["target"],
            attempt=row["attempt"],
            signal=row["signal"],
            kind=EvidenceKind(row["kind"]) if row["kind"] else None,
            status=row["status"],
            location=row["location"],
            candidate_url=row["candidate_url"],
            authority=SourceAuthority(row["authority"]) if row["authority"] else None,
            trustworthy=bool(row["trustworthy"]),
            detail=row["detail"],
        )
        for row in rows
    )


def _reconciliation_key(record: InterpretedObservation) -> tuple:
    """Identity excluding local sequence so copied duplicate evidence stays idempotent."""

    return (
        record.observed_at,
        record.stage,
        record.channel,
        record.target,
        record.attempt,
        record.signal,
        record.kind,
        record.status,
        record.location,
        record.candidate_url,
        record.authority,
        record.trustworthy,
        record.detail,
    )


def reconcile_duplicate_availability(
    conn: sqlite3.Connection, *, updated_at: str
) -> int:
    """Project evidence from strong URL duplicates onto their visible survivor.

    URL deduplication is already the repository's high-confidence posting-identity
    boundary.  Availability may therefore compare normalized source/network evidence
    inside that boundary without guessing that title-similar roles are identical.
    User-confirmed or pending replacement workflows remain owned by their survivor and
    are deliberately left untouched.
    """

    links = conn.execute(
        "SELECT posting_id, duplicate_of FROM postings "
        "WHERE duplicate_of IS NOT NULL ORDER BY duplicate_of, posting_id"
    ).fetchall()
    groups: dict[str, list[str]] = {}
    for row in links:
        groups.setdefault(row["duplicate_of"], []).append(row["posting_id"])

    reconciled = 0
    for survivor_id, duplicate_ids in groups.items():
        posting_ids = [survivor_id, *duplicate_ids]
        marks = ",".join("?" for _ in posting_ids)
        states = {
            row["posting_id"]: row
            for row in conn.execute(
                f"SELECT * FROM availability_state WHERE posting_id IN ({marks})",
                posting_ids,
            )
        }
        completed = {
            posting_id: state
            for posting_id, state in states.items()
            if state["validation_completed"]
        }
        if len(completed) < 2:
            continue
        survivor_state = states.get(survivor_id)
        if survivor_state and (
            survivor_state["pending_candidate_url"] or survivor_state["confirmed_url"]
        ):
            continue
        if survivor_state is None:
            authority = SourceAuthority(
                next(iter(completed.values()))["source_authority"]
            )
            ensure_pending(conn, survivor_id, authority, now=updated_at)
            survivor_state = get_state(conn, survivor_id)
        assert survivor_state is not None

        owned_records: list[tuple[str, InterpretedObservation]] = []
        for posting_id in completed:
            for record in load_current_records(conn, posting_id):
                if (
                    record.channel in {ObservationChannel.NETWORK, ObservationChannel.SOURCE}
                    or posting_id == survivor_id
                ):
                    owned_records.append((posting_id, record))
        if not owned_records:
            continue

        unique: dict[tuple, tuple[str, InterpretedObservation]] = {}
        for owned in owned_records:
            unique.setdefault(_reconciliation_key(owned[1]), owned)
        ordered = sorted(
            unique.values(),
            key=lambda item: (
                item[1].observed_at,
                item[1].sequence,
                item[0],
                item[1].channel.value,
                item[1].signal,
            ),
        )
        records = tuple(
            replace(record, sequence=index)
            for index, (_posting_id, record) in enumerate(ordered, start=1)
        )
        has_investigation = any(
            record.channel is ObservationChannel.INVESTIGATOR for record in records
        )
        stages = (
            tuple(json.loads(survivor_state["investigation_stages"] or "[]"))
            if has_investigation
            else ()
        )
        evaluation = evaluate_interpreted_availability(
            source_authority=SourceAuthority(survivor_state["source_authority"]),
            user_state=user_state_for_posting(conn, survivor_id),
            records=records,
            investigation_stages=stages,
        )
        persist_evaluation(
            conn,
            survivor_id,
            evaluation,
            updated_at=updated_at,
        )
        reconciled += 1
    return reconciled


def _user_state(
    *,
    application_row_id: str | None,
    status: str | None,
    notes: str | None,
    applied_date: str | None,
) -> UserState:
    if status in PIPELINE_STATUSES:
        return UserState.APPLIED
    if status == "Interested" or (application_row_id and ((notes or "").strip() or applied_date)):
        return UserState.INTERESTED
    return UserState.ORDINARY


def user_state_for_posting(
    conn: sqlite3.Connection, posting_id: str
) -> UserState:
    row = conn.execute(
        "SELECT posting_id AS application_row_id, status, notes, applied_date "
        "FROM applications WHERE posting_id = ?",
        (posting_id,),
    ).fetchone()
    if row is None:
        return UserState.ORDINARY
    return _user_state(
        application_row_id=row["application_row_id"],
        status=row["status"],
        notes=row["notes"],
        applied_date=row["applied_date"],
    )


def _current_records(
    conn: sqlite3.Connection, posting_ids: Iterable[str]
) -> dict[str, list[sqlite3.Row]]:
    ids = list(dict.fromkeys(posting_ids))
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in conn.execute(
        f"SELECT * FROM availability_evidence "
        f"WHERE is_current = 1 AND posting_id IN ({marks}) ORDER BY id",
        ids,
    ):
        grouped.setdefault(row["posting_id"], []).append(row)
    return grouped


def _rows_for_target(rows: list[sqlite3.Row], target: str) -> list[sqlite3.Row]:
    """Select one logical check, following only its recorded redirect chain."""

    related_targets = {target}
    selected: list[sqlite3.Row] = []
    for row in rows:
        if row["target"] not in related_targets:
            continue
        selected.append(row)
        if row["location"]:
            related_targets.add(row["location"])
    return selected


def _decision_from_rows(
    state,
    rows: list[sqlite3.Row],
    *,
    user_state: UserState,
) -> AvailabilityDecision:
    keys = set(state.keys())

    def state_value(name: str):
        if name in keys:
            return state[name]
        return state[f"availability_{name}"]

    confirmed = state_value("confirmed_url")
    pending = state_value("pending_candidate_url")
    if confirmed and not pending:
        policy_rows = [
            row
            for row in _rows_for_target(rows, confirmed)
            if row["kind"] is not None
        ]
    else:
        policy_rows = [row for row in rows if row["kind"] is not None]
    observations = tuple(
        EvidenceObservation(
            kind=EvidenceKind(row["kind"]),
            attempt=row["attempt"],
            candidate_url=row["candidate_url"],
            subject=row["target"],
        )
        for row in policy_rows
    )
    if pending and not any(
        item.kind is EvidenceKind.REPLACEMENT_FOUND for item in observations
    ):
        observations += (
            EvidenceObservation(
                EvidenceKind.REPLACEMENT_FOUND,
                attempt=max((item.attempt for item in observations), default=1),
                candidate_url=pending,
                subject="pending_replacement",
            ),
        )
    if not observations:
        observations = (EvidenceObservation(EvidenceKind.USER_OUTAGE),)
    stages = tuple(json.loads(state_value("investigation_stages") or "[]"))
    if confirmed and not pending:
        stages = ()
    authority = state_value("source_authority")
    if confirmed and not pending:
        authority = (
            state_value("confirmed_url_authority")
            or SourceAuthority.UNKNOWN.value
        )
    evidence = AvailabilityEvidence(
        source_authority=SourceAuthority(authority),
        user_state=user_state,
        observations=observations,
        investigation_stages=stages,
    )
    return decide_availability(evidence)


def get_projection(
    conn: sqlite3.Connection, posting_id: str
) -> AvailabilityProjection:
    row = conn.execute(
        """
        SELECT p.posting_id, p.url AS original_url,
               a.posting_id AS application_row_id, a.status AS application_status,
               a.notes AS application_notes, a.applied_date AS application_date,
               s.*
        FROM postings p
        JOIN availability_state s ON s.posting_id = p.posting_id
        LEFT JOIN applications a ON a.posting_id = p.posting_id
        WHERE p.posting_id = ?
        """,
        (posting_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"posting has no availability state: {posting_id}")
    records = _current_records(conn, [posting_id]).get(posting_id, [])
    decision = _decision_from_rows(
        row,
        records,
        user_state=_user_state(
            application_row_id=row["application_row_id"],
            status=row["application_status"],
            notes=row["application_notes"],
            applied_date=row["application_date"],
        ),
    )
    completed = bool(row["validation_completed"])
    return AvailabilityProjection(
        posting_id=posting_id,
        decision=decision,
        visible=completed and decision.board_treatment is not BoardTreatment.ARCHIVED,
        original_url=row["original_url"],
        effective_url=row["confirmed_url"] or row["original_url"],
        validation_completed=completed,
        confirmed_url=row["confirmed_url"],
    )


def project_board_rows(
    conn: sqlite3.Connection,
    rows: Iterable[sqlite3.Row],
    *,
    include_archived: bool = False,
) -> list[dict]:
    """Enrich legacy Board rows and filter only availability-archived postings."""

    raw_rows = list(rows)
    records = _current_records(
        conn,
        [row["posting_id"] for row in raw_rows if row["availability_state_id"]],
    )
    projected: list[dict] = []
    for row in raw_rows:
        item = dict(row)
        item["original_url"] = row["url"]
        if not row["availability_state_id"]:
            legacy_live = bool(row["is_active"])
            item.update(
                availability=("live" if legacy_live else "closed"),
                board_treatment=("normal" if legacy_live else "visible_closed"),
                primary_action=("apply" if legacy_live else "none"),
                secondary_action=("none" if legacy_live else "open_original"),
                replacement_candidate_url=None,
                availability_checked_at=None,
                availability_pending=False,
                confirmed_url=None,
                effective_url=row["url"],
            )
            projected.append(item)
            continue
        if not row["availability_validation_completed"]:
            continue
        decision = _decision_from_rows(
            row,
            records.get(row["posting_id"], []),
            user_state=_user_state(
                application_row_id=row["application_row_id"],
                status=row["status"],
                notes=row["notes"],
                applied_date=row["applied_date"],
            ),
        )
        if decision.board_treatment is BoardTreatment.ARCHIVED and not include_archived:
            continue
        effective_url = row["availability_confirmed_url"] or row["url"]
        item.update(
            availability=decision.availability.value,
            board_treatment=decision.board_treatment.value,
            primary_action=decision.primary_action.value,
            secondary_action=decision.secondary_action.value,
            replacement_candidate_url=decision.replacement_candidate_url,
            availability_checked_at=row["availability_last_checked_at"],
            availability_pending=False,
            confirmed_url=row["availability_confirmed_url"],
            effective_url=effective_url,
            url=effective_url,
        )
        projected.append(item)
    return projected


def _candidate_authority_from_history(
    conn: sqlite3.Connection, posting_id: str, candidate_url: str
) -> str | None:
    row = conn.execute(
        """
        SELECT authority
        FROM availability_evidence
        WHERE posting_id = ? AND candidate_url = ? AND authority IS NOT NULL
        ORDER BY is_current DESC, id DESC
        LIMIT 1
        """,
        (posting_id, candidate_url),
    ).fetchone()
    return row["authority"] if row else None


def confirm_replacement(
    conn: sqlite3.Connection, posting_id: str, *, confirmed_at: str
) -> str:
    """Persist the server-side pending candidate as the effective URL override."""

    state = get_state(conn, posting_id)
    if state is None:
        raise LookupError(f"posting has no availability state: {posting_id}")
    candidate = state["pending_candidate_url"]
    if not candidate:
        if state["confirmed_url"]:
            return state["confirmed_url"]
        raise ValueError("posting has no pending replacement candidate")
    authority = state["pending_candidate_authority"] or _candidate_authority_from_history(
        conn, posting_id, candidate
    )
    cur = conn.execute(
        """
        UPDATE availability_state
        SET confirmed_url = ?, confirmed_url_authority = ?,
            replacement_confirmed_at = ?, pending_candidate_url = NULL,
            pending_candidate_authority = NULL, updated_at = ?
        WHERE posting_id = ? AND pending_candidate_url = ?
        """,
        (
            candidate,
            authority or SourceAuthority.UNKNOWN.value,
            confirmed_at,
            confirmed_at,
            posting_id,
            candidate,
        ),
    )
    if cur.rowcount != 1:
        conn.rollback()
        raise RuntimeError("replacement candidate changed during confirmation")
    conn.commit()
    return candidate


def undo_replacement_confirmation(
    conn: sqlite3.Connection, posting_id: str, *, updated_at: str
) -> bool:
    """Revert the effective URL and re-offer the last confirmed candidate."""

    cur = conn.execute(
        """
        UPDATE availability_state
        SET pending_candidate_url = COALESCE(pending_candidate_url, confirmed_url),
            pending_candidate_authority = CASE
                WHEN pending_candidate_url IS NULL THEN confirmed_url_authority
                ELSE pending_candidate_authority
            END,
            confirmed_url = NULL, confirmed_url_authority = NULL,
            replacement_confirmed_at = NULL, updated_at = ?
        WHERE posting_id = ? AND confirmed_url IS NOT NULL
        """,
        (updated_at, posting_id),
    )
    conn.commit()
    return cur.rowcount == 1
