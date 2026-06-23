"""Scheduled collector (v2): fetch every source (isolated), capture raw payloads, store
as `pending`, compute keyword priority-hint flags, and email a review nudge once enough
pile up. No classification happens here — that's the on-demand `/review-internships` skill.

One invocation = one cycle. Run hourly via launchd. Per-source failures are isolated and
recorded; only a source that succeeded (ok AND count>0) has its postings closed.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from internshelper import classify, clock, config, db, mailer, notify, store
from internshelper.config import Settings, SourceEntry
from internshelper.connectors import build_connector
from internshelper.dotenv import feature_enabled, load_dotenv


@dataclass
class CollectResult:
    pending: int
    candidates: int
    sent: bool


def process_source(
    conn: sqlite3.Connection,
    entry: SourceEntry,
    compiled: dict,
    now: str,
    payloads_dir: str | Path | None,
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
    postings = [p for p in postings if entry.accepts(p.title)]
    dropped = fetched - len(postings)  # counted (not silent) so the Health tab can show it

    seen = set()
    for posting in postings:
        classify.classify(posting, compiled)  # keyword priority hints only — not a gate
        store.upsert(conn, posting, now, payloads_dir=payloads_dir)
        seen.add(posting.posting_id)
    store.record_run(conn, entry.source_key, ok=True, count=len(postings), error=None,
                     now=now, dropped=dropped)
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
) -> CollectResult:
    # Heartbeat first, before any (possibly slow) fetch.
    store.record_run(conn, "run", ok=True, count=len(sources), error=None, now=now)
    compiled = classify.compile_keywords(settings.keywords)
    for entry in sources:
        process_source(conn, entry, compiled, now, payloads_dir)

    pending, candidates = store.pending_counts(conn)
    sent = False
    already = (db.get_meta(conn, "pending_notified") or "0") == "1"
    if email_enabled and pending >= settings.notify_threshold and not already:
        try:
            notify.send_nudge(settings, pending, candidates, password, send_fn=send_fn)
            db.set_meta(conn, "pending_notified", "1")
            store.record_run(conn, "notify", ok=True, count=pending, error=None, now=now)
            sent = True
        except Exception as e:
            store.record_run(conn, "notify", ok=False, count=0, error=str(e), now=now)
    return CollectResult(pending=pending, candidates=candidates, sent=sent)


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

    result = run_cycle(conn, settings, sources, now, password, payloads_dir,
                       email_enabled=feature_enabled("EMAIL"))
    print(
        f"internsHELPer: collected — sources={len(sources)} "
        f"pending={result.pending} (candidates={result.candidates}) nudge_sent={result.sent}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
