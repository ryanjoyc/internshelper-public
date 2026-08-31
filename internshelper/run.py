"""Scheduled collector: fetch every source (isolated), capture raw payloads, store
straight into the Inbox (no approval gate), compute keyword priority-hint flags,
rescore + regroup, and email the Top-target digest for never-digested rows.

One invocation = one cycle. Run hourly via launchd. Per-source failures are isolated and
recorded; only a source that succeeded (ok AND count>0) has its postings closed.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from internshelper import classify, clock, config, db, dedup, mailer, notify, ranking, store, tiers
from internshelper.availability import AvailabilityStatus, EvidenceKind, SourceAuthority
from internshelper.availability_checks import (
    CheckStage,
    NetworkFailure,
    NetworkObservation,
    SourceObservation,
    evaluate_availability,
    interpret_network,
)
from internshelper.availability_runtime import HttpDestinationChecker
from internshelper.availability_store import (
    ensure_pending,
    get_state,
    load_current_records,
    persist_evaluation,
    reconcile_duplicate_availability,
    user_state_for_posting,
)
from internshelper.config import Settings, SourceEntry
from internshelper.connectors import build_connector
from internshelper.dotenv import feature_enabled, load_dotenv


@dataclass
class CollectResult:
    inbox: int
    digested: int   # Top-target rows emailed (and stamped) this cycle
    sent: bool
    rescored: int = 0
    deduped: int = 0   # duplicate rows collapsed (hidden) this cycle


class AvailabilityChecker(Protocol):
    def check(self, url: str, **kwargs) -> tuple[NetworkObservation, ...]: ...


_COMMUNITY_SOURCE_TYPES = {"github", "markdown"}


def _source_authority(entry: SourceEntry) -> SourceAuthority:
    return (
        SourceAuthority.COMMUNITY_LIST
        if entry.type in _COMMUNITY_SOURCE_TYPES
        else SourceAuthority.FIRST_PARTY_ATS
    )


def _one_hour_after(now: str) -> str:
    return (datetime.fromisoformat(now) + timedelta(hours=1)).isoformat()


def _check_is_due(state, now: str) -> bool:
    if state is None or not state["validation_completed"]:
        return True
    if state["status"] == AvailabilityStatus.CLOSED.value:
        # A connector seeing a previously closed posting again is recovery evidence.
        return True
    return bool(state["next_check_at"] and state["next_check_at"] <= now)


def _check_posting_availability(
    conn: sqlite3.Connection,
    posting,
    entry: SourceEntry,
    *,
    now: str,
    checker: AvailabilityChecker,
    is_new: bool,
) -> None:
    authority = _source_authority(entry)
    state = get_state(conn, posting.posting_id)
    if state is None:
        if not is_new:
            # Existing pre-availability rows stay compatible; backfill is a separate,
            # explicitly paced operation rather than a surprise request flood.
            return
        ensure_pending(conn, posting.posting_id, authority, now=now)
        state = get_state(conn, posting.posting_id)
    assert state is not None
    if not _check_is_due(state, now):
        return

    was_closed = state["status"] == AvailabilityStatus.CLOSED.value
    previous = load_current_records(conn, posting.posting_id)
    attempt = int(state["last_attempt"] or 0) + 1
    sequence = 1 if not previous else max(item.sequence for item in previous) + 1
    stage = (
        CheckStage.INITIAL_VALIDATION
        if not previous
        else CheckStage.SCHEDULED_RETRY
    )
    employer_hosted = authority is SourceAuthority.FIRST_PARTY_ATS
    try:
        observations = checker.check(
            posting.url,
            sequence=sequence,
            attempt=attempt,
            stage=stage,
            observed_at=now,
            employer_hosted=employer_hosted,
            target="original_url",
        )
        if not observations:
            raise RuntimeError("availability checker returned no observations")
    except Exception as exc:
        store.record_run(
            conn,
            f"availability:{posting.posting_id}",
            ok=False,
            count=0,
            error=f"{type(exc).__name__}: {exc}",
            now=now,
        )
        observations = (
            NetworkObservation(
                sequence=sequence,
                observed_at=now,
                stage=stage,
                target="original_url",
                attempt=attempt,
                failure=NetworkFailure.CONNECTION,
                employer_hosted=employer_hosted,
            ),
        )
    if was_closed and any(
        interpret_network(item).kind is EvidenceKind.DESTINATION_LIVE
        for item in observations
    ):
        # Reappearance on a source is not enough to undo closure; a reachable live
        # destination is. Once recovered, stale hard failures leave the current window.
        previous = ()
    evaluation = evaluate_availability(
        source_authority=authority,
        user_state=user_state_for_posting(conn, posting.posting_id),
        observations=observations,
        previous_records=previous,
    )
    next_check_at = (
        _one_hour_after(now)
        if evaluation.decision.availability is AvailabilityStatus.UNCERTAIN
        and attempt < 2
        and not evaluation.decision.replacement_candidate_url
        else None
    )
    persist_evaluation(
        conn,
        posting.posting_id,
        evaluation,
        updated_at=now,
        next_check_at=next_check_at,
    )


def _record_source_absences(
    conn: sqlite3.Connection,
    entry: SourceEntry,
    seen_ids: set[str],
    *,
    now: str,
    checker: AvailabilityChecker,
) -> None:
    """Append complete source omissions for already availability-tracked rows."""

    # A zero-row connector result has no independent completeness proof. Existing
    # quiet-source diagnostics handle it; do not turn a parser/schema failure into
    # two hours of automatic closures.
    if not seen_ids:
        return
    unseen_clause = ""
    params: list[object] = [entry.source_key, AvailabilityStatus.CLOSED.value]
    if seen_ids:
        marks = ",".join("?" for _ in seen_ids)
        unseen_clause = f"AND p.posting_id NOT IN ({marks})"
        params.extend(sorted(seen_ids))
    rows = conn.execute(
        f"""
        SELECT p.posting_id, p.url,
               COALESCE(av.confirmed_url, p.url) AS effective_url,
               av.last_attempt, av.status
        FROM postings p
        JOIN availability_state av ON av.posting_id = p.posting_id
        WHERE p.source_key = ? AND av.validation_completed = 1
          AND (av.status IS NULL OR av.status != ?)
          {unseen_clause}
        """,
        params,
    ).fetchall()
    authority = _source_authority(entry)
    for row in rows:
        current = load_current_records(conn, row["posting_id"])
        # A complete first-party omission supersedes a stale earlier live-page check.
        # Retain only prior omissions so the approved one/two-attempt threshold can
        # advance without old liveness becoming a false contemporaneous conflict.
        if authority is SourceAuthority.FIRST_PARTY_ATS:
            previous = tuple(
                item
                for item in current
                if item.kind is EvidenceKind.FIRST_PARTY_ABSENT
                and item.target == entry.source_key
            )
        else:
            # A list removal is not closure proof, but repeated hard failures at the
            # destination are. Keep only those attempts; discard stale liveness and
            # list-presence evidence before evaluating the new removal.
            previous = tuple(
                item
                for item in current
                if item.kind in {
                    EvidenceKind.DESTINATION_NOT_FOUND,
                    EvidenceKind.DESTINATION_GONE,
                }
                and item.target == "original_url"
            )
        sequence = max((item.sequence for item in current), default=0) + 1
        attempt = int(row["last_attempt"] or 0) + 1
        source_observation = SourceObservation(
            sequence=sequence,
            observed_at=now,
            stage=CheckStage.SCHEDULED_RETRY,
            target=entry.source_key,
            attempt=attempt,
            authority=authority,
            complete=True,
            original_present=False,
        )
        observations: tuple = (source_observation,)
        if authority is SourceAuthority.COMMUNITY_LIST:
            try:
                destination = checker.check(
                    row["effective_url"],
                    sequence=sequence + 1,
                    attempt=attempt,
                    stage=CheckStage.SCHEDULED_RETRY,
                    observed_at=now,
                    employer_hosted=False,
                    target="original_url",
                )
                if not destination:
                    raise RuntimeError("availability checker returned no observations")
            except Exception as exc:
                store.record_run(
                    conn,
                    f"availability:{row['posting_id']}",
                    ok=False,
                    count=0,
                    error=f"{type(exc).__name__}: {exc}",
                    now=now,
                )
                destination = (
                    NetworkObservation(
                        sequence=sequence + 1,
                        observed_at=now,
                        stage=CheckStage.SCHEDULED_RETRY,
                        target="original_url",
                        attempt=attempt,
                        failure=NetworkFailure.CONNECTION,
                        employer_hosted=False,
                    ),
                )
            observations = (source_observation, *destination)
        evaluation = evaluate_availability(
            source_authority=authority,
            user_state=user_state_for_posting(conn, row["posting_id"]),
            observations=observations,
            previous_records=previous,
        )
        persist_evaluation(
            conn,
            row["posting_id"],
            evaluation,
            updated_at=now,
        )


def process_source(
    conn: sqlite3.Connection,
    entry: SourceEntry,
    compiled: dict,
    now: str,
    payloads_dir: str | Path | None,
    availability_checker: AvailabilityChecker | None = None,
) -> bool:
    """Fetch + classify(hint) + store one source. Returns True on success.

    On any error (including a timeout) records runs(ok=false) and closes nothing.
    """
    try:
        postings = build_connector(entry).fetch()
    except Exception as e:
        store.record_run(
            conn, entry.source_key, ok=False, count=0,
            error=f"{type(e).__name__}: {e}", now=now,
        )
        return False

    # Coarse per-source flood guard (the "Arby's" filter): drop titles the source opted out of.
    fetched = len(postings)
    enumerated_ids = {posting.posting_id for posting in postings}
    postings = [p for p in postings if entry.accepts(p.title)]
    dropped = fetched - len(postings)  # counted (not silent) so the Health tab can show it

    seen = set()
    for posting in postings:
        is_new = conn.execute(
            "SELECT 1 FROM postings WHERE posting_id = ?", (posting.posting_id,)
        ).fetchone() is None
        classify.classify(posting, compiled)  # keyword priority hints only — not a gate
        atomic_pending = availability_checker is not None and is_new
        try:
            store.upsert(
                conn,
                posting,
                now,
                payloads_dir=payloads_dir,
                commit=not atomic_pending,
            )
            if atomic_pending:
                ensure_pending(
                    conn,
                    posting.posting_id,
                    _source_authority(entry),
                    now=now,
                )
        except Exception:
            if atomic_pending:
                conn.rollback()
            raise
        seen.add(posting.posting_id)
        if availability_checker is not None:
            _check_posting_availability(
                conn,
                posting,
                entry,
                now=now,
                checker=availability_checker,
                is_new=is_new,
            )
    store.record_run(conn, entry.source_key, ok=True, count=len(postings), error=None,
                     now=now, dropped=dropped)
    if availability_checker is not None:
        _record_source_absences(
            conn,
            entry,
            enumerated_ids,
            now=now,
            checker=availability_checker,
        )
    store.apply_close_detection(conn, entry.source_key, seen, ok=True, count=len(postings))
    return True


def run_cycle(
    conn: sqlite3.Connection,
    settings: Settings,
    sources: list[SourceEntry],
    now: str,
    password: str | None,
    payloads_dir: str | Path | None,
    send_fn=mailer.send,
    email_enabled: bool = True,
    availability_checker: AvailabilityChecker | None = None,
) -> CollectResult:
    # Heartbeat first, before any (possibly slow) fetch.
    store.record_run(conn, "run", ok=True, count=len(sources), error=None, now=now)
    compiled = classify.compile_keywords(settings.keywords)
    for entry in sources:
        process_source(
            conn,
            entry,
            compiled,
            now,
            payloads_dir,
            availability_checker=availability_checker,
        )

    # Cross-source de-dup: collapse strong URL-match duplicates before ranking/tiers so the
    # user (and the digest) never see the same job twice. Best-effort — never break the collect.
    deduped = 0
    try:
        deduped = dedup.collapse_url_duplicates(conn)
    except Exception as e:
        store.record_run(conn, "dedup", ok=False, count=0,
                         error=f"{type(e).__name__}: {e}", now=now)

    if availability_checker is not None:
        try:
            reconcile_duplicate_availability(conn, updated_at=now)
        except Exception as e:
            store.record_run(
                conn,
                "availability:reconcile",
                ok=False,
                count=0,
                error=f"{type(e).__name__}: {e}",
                now=now,
            )

    # Learned ranking + tiers: score and tier every inbox row before the user sees it.
    # A ranking failure must never break the collect — record it and move on.
    rescored = 0
    try:
        rescored = ranking.rescore_inbox(conn, now)
        tiers.retier_inbox(conn, tiers.load_tier_map())
    except Exception as e:
        store.record_run(conn, "rank", ok=False, count=0,
                         error=f"{type(e).__name__}: {e}", now=now)

    # Top-target digest: exactly-once per posting (notified_at watermark). A send
    # failure leaves the rows unstamped, so the next cycle retries them.
    new_top = [
        row
        for row in store.inbox_with_status(conn)
        if row["tier"] == "top_target"
        and row["notified_at"] is None
        and row["primary_action"] == "apply"
    ]
    sent = False
    digested = 0
    if email_enabled and new_top:
        try:
            notify.send_digest(settings, new_top, password, send_fn=send_fn)
            conn.executemany(
                "UPDATE postings SET notified_at = ? WHERE posting_id = ?",
                [(now, r["posting_id"]) for r in new_top],
            )
            conn.commit()
            store.record_run(conn, "notify", ok=True, count=len(new_top), error=None, now=now)
            sent, digested = True, len(new_top)
        except Exception as e:
            store.record_run(conn, "notify", ok=False, count=0, error=str(e), now=now)
    return CollectResult(inbox=store.inbox_count(conn), digested=digested, sent=sent,
                         rescored=rescored, deduped=deduped)


def main(argv=None) -> int:
    load_dotenv()
    if not feature_enabled("COLLECT"):
        print("internsHELPer: collect disabled (INTERNSHELPER_FEATURE_COLLECT=0)")
        return 0

    sources_path = config.default_path("INTERNSHELPER_SOURCES", "config/sources.yaml")
    settings_path = config.default_path("INTERNSHELPER_SETTINGS", "config/settings.toml")
    db_path = config.default_path("INTERNSHELPER_DB", "data/internshelper.db")
    password = os.environ.get("INTERNSHELPER_SMTP_PASSWORD")

    settings = config.load_settings(settings_path)
    sources, errors = config.load_sources(sources_path)

    conn = db.connect(db_path)
    db.init_db(conn)
    now = clock.now_iso()
    db.prune_runs(conn, now=now, days=30)
    payloads_dir = Path(db_path).parent / "payloads"

    for err in errors:  # surface malformed source entries on the Health tab
        store.record_run(conn, "config", ok=False, count=0, error=err["reason"], now=now)

    checker = (
        HttpDestinationChecker()
        if feature_enabled("AVAILABILITY")
        else None
    )
    try:
        result = run_cycle(
            conn,
            settings,
            sources,
            now,
            password,
            payloads_dir,
            email_enabled=feature_enabled("EMAIL"),
            availability_checker=checker,
        )
    finally:
        if checker is not None:
            checker.close()
    print(
        f"internsHELPer: collected — sources={len(sources)} inbox={result.inbox} "
        f"deduped={result.deduped} rescored={result.rescored} digested={result.digested}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
