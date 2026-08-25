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
from pathlib import Path

from internshelper import classify, clock, config, db, dedup, mailer, notify, ranking, store, tiers
from internshelper.config import Settings, SourceEntry
from internshelper.connectors import build_connector
from internshelper.dotenv import feature_enabled, load_dotenv


@dataclass
class CollectResult:
    inbox: int
    digested: int   # apply-first rows emailed (and stamped) this cycle
    sent: bool
    rescored: int = 0
    deduped: int = 0   # duplicate rows collapsed (hidden) this cycle


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

    # Cross-source de-dup: collapse strong URL-match duplicates before ranking/tiers so the
    # user (and the digest) never see the same job twice. Best-effort — never break the collect.
    deduped = 0
    try:
        deduped = dedup.collapse_url_duplicates(conn)
    except Exception as e:
        store.record_run(conn, "dedup", ok=False, count=0,
                         error=f"{type(e).__name__}: {e}", now=now)

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
    new_top = conn.execute(
        "SELECT posting_id, title, company, url FROM postings "
        "WHERE tier = 'top_target' AND notified_at IS NULL "
        f"AND is_active = 1 AND {store.INBOX_SQL} ORDER BY posting_id"
    ).fetchall()
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

    result = run_cycle(conn, settings, sources, now, password, payloads_dir,
                       email_enabled=feature_enabled("EMAIL"))
    print(
        f"internsHELPer: collected — sources={len(sources)} inbox={result.inbox} "
        f"deduped={result.deduped} rescored={result.rescored} digested={result.digested}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
