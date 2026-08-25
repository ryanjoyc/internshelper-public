"""Inbox actions — the testable layer the Board routes and the tier-auditor skills drive.

There is no approval gate: every collected posting is in the Inbox, and curation is
dismiss (hide + negative label), pin (sticky tier + directional label), flag (park
suspect data for investigation — hidden, NO label), and the bulk hygiene sweeps
(guard leaks, closed postings — undoable batches). The agent skills call
`list-inbox`/`list-flagged`, read payloads, then `pin`/`dismiss`/`unflag` per
posting. `set_verdict`/`reset_verdict` survive as the internal verdict primitives
dismiss is built on.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from internshelper import clock, config, db, store, text, tiers
from internshelper.dotenv import load_dotenv

VERDICTS = ("match", "no_match")

DISMISS_REASON = "dismissed: board"

GUARD_LEAK_REASON = "bulk: guard leak"
CLOSED_REASON = "bulk: closed on source"

# Payload keys tried (in order) for a human-readable description, across connector shapes.
_DESCRIPTION_KEYS = ("content", "description", "descriptionPlain", "descriptionHtml", "plain")


def set_verdict(
    conn: sqlite3.Connection, posting_id: str, verdict: str, reason: str, now: str
) -> None:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}, got {verdict!r}")
    conn.execute(
        "UPDATE postings SET verdict = ?, verdict_reason = ?, reviewed_at = ?, "
        "review_status = 'reviewed' WHERE posting_id = ?",
        (verdict, reason, now, posting_id),
    )
    conn.commit()


def reset_verdict(conn: sqlite3.Connection, posting_id: str) -> None:
    """Undo a verdict: the posting returns to the pending queue as if never reviewed.

    Never touches meta. If the undone verdict was the one that emptied the queue,
    `finish()` already reset `pending_notified` — that's fine: the nudge only re-fires
    when pending reaches the notify threshold again, so a lone undone item can't
    trigger a duplicate email by itself.
    """
    conn.execute(
        "UPDATE postings SET verdict = NULL, verdict_reason = NULL, reviewed_at = NULL, "
        "review_status = 'pending' WHERE posting_id = ?",
        (posting_id,),
    )
    conn.commit()


def _applied_ids(conn: sqlite3.Connection) -> set[str]:
    return {r["posting_id"] for r in conn.execute("SELECT posting_id FROM applications")}


def find_guard_leaks(conn: sqlite3.Connection, entries) -> list[dict]:
    """Inbox rows whose title no longer passes their source's CURRENT title guard.

    Catches postings collected before a guard was fixed/tightened (e.g. the substring-era
    'Internal…' leaks). Never flags: sources absent from the loaded config (removed),
    guardless sources (accepts() is trivially True), or posting_ids the user applied to.
    """
    by_key = {e.source_key: e for e in entries}
    applied_ids = _applied_ids(conn)
    rows = conn.execute(
        "SELECT posting_id, title, company, source_key FROM postings "
        f"WHERE {store.INBOX_SQL} ORDER BY source_key, posting_id"
    )
    return [
        dict(r)
        for r in rows
        if (e := by_key.get(r["source_key"])) is not None
        and e.title_must_match
        and not e.accepts(r["title"])
        and r["posting_id"] not in applied_ids
    ]


def _clear_batch(conn: sqlite3.Connection, ids: list[str], reason: str, now: str) -> int:
    """Bulk-dismiss the given inbox posting_ids as one undoable batch (stamp + reason)."""
    if not ids:
        return 0
    ph = ",".join("?" * len(ids))
    cur = conn.execute(
        "UPDATE postings SET verdict = 'no_match', verdict_reason = ?, reviewed_at = ?, "
        f"review_status = 'reviewed' WHERE {store.INBOX_SQL} AND posting_id IN ({ph})",
        (reason, now, *ids),
    )
    conn.commit()
    return cur.rowcount


def clear_guard_leaks(
    conn: sqlite3.Connection, entries, now: str, reason: str = GUARD_LEAK_REASON
) -> int:
    """Bulk no_match every guard leak. Undo via `undo_bulk_clear(conn, now, reason)`."""
    ids = [r["posting_id"] for r in find_guard_leaks(conn, entries)]
    return _clear_batch(conn, ids, reason, now)


def find_closed_inbox(conn: sqlite3.Connection) -> list[dict]:
    """Inbox rows whose posting vanished from its source board (close-detection).

    They can't be applied to anymore, yet they linger in the Inbox because the inbox
    queries ignore `is_active` (a closed pill marks them instead — close-detection
    has false positives, so nothing auto-dismisses). Applied posting_ids are never
    flagged.
    """
    applied_ids = _applied_ids(conn)
    rows = conn.execute(
        "SELECT posting_id, title, company, source_key FROM postings "
        f"WHERE {store.INBOX_SQL} AND is_active = 0 ORDER BY source_key, posting_id"
    )
    return [dict(r) for r in rows if r["posting_id"] not in applied_ids]


def clear_closed_inbox(
    conn: sqlite3.Connection, now: str, reason: str = CLOSED_REASON
) -> int:
    """Bulk-dismiss every closed-but-inbox row. Undo via `undo_bulk_clear`."""
    ids = [r["posting_id"] for r in find_closed_inbox(conn)]
    return _clear_batch(conn, ids, reason, now)


def undo_bulk_clear(conn: sqlite3.Connection, reviewed_at: str, reason: str) -> int:
    """Revert one bulk batch (matched by its shared stamp + reason) to pending.

    Covers both the no_match clears and the tier-accept match batch — the stamp +
    reason pair uniquely identifies a batch regardless of its verdict.
    """
    cur = conn.execute(
        "UPDATE postings SET verdict = NULL, verdict_reason = NULL, reviewed_at = NULL, "
        "review_status = 'pending' "
        "WHERE verdict IS NOT NULL AND reviewed_at = ? AND verdict_reason = ?",
        (reviewed_at, reason),
    )
    conn.commit()
    return cur.rowcount


# ---------- inbox actions (company-grouped Board: no gate, just curation) ----------

_INBOX_FIELDS = (
    "p.posting_id, p.source_key, p.title, p.company, p.location, p.url, p.payload_path, "
    "p.posted_at, p.first_seen, p.is_internship, p.is_newgrad, p.is_cs_relevant, "
    "p.rank_score, p.rank_reasons, p.is_active, "
    "p.tier, p.pinned_tier"
)


def _inbox_where(
    q: str = "", source: str = "", tier: str | None = None
) -> tuple[str, list[object]]:
    ph = ",".join("?" * len(store.PIPELINE_STATUSES))
    clauses = [store.INBOX_SQL, f"(a.status IS NULL OR a.status NOT IN ({ph}))"]
    params: list[object] = list(store.PIPELINE_STATUSES)
    if q:
        clauses.append("(LOWER(p.title) LIKE ? OR LOWER(p.company) LIKE ?)")
        like = f"%{q.lower()}%"
        params += [like, like]
    if source:
        clauses.append("p.source_key = ?")
        params.append(source)
    if tier:
        clauses.append("p.tier = ?")
        params.append(tier)
    return " WHERE " + " AND ".join(clauses), params


def list_inbox(
    conn: sqlite3.Connection,
    limit: int | None = None,
    offset: int = 0,
    *,
    q: str = "",
    source: str = "",
    tier: str | None = None,
) -> list[dict]:
    """Inbox postings (non-dismissed, not yet in the pipeline), best-first.

    `tier` filters on the authoritative company group. Legacy `pinned_tier` remains
    visible for compatibility but cannot override company classification.
    """
    where, params = _inbox_where(q, source, tier)
    sql = (
        f"SELECT {_INBOX_FIELDS} FROM postings p "
        f"LEFT JOIN applications a ON a.posting_id = p.posting_id{where} "
        "ORDER BY (p.rank_score IS NULL), p.rank_score DESC, "
        f"{store.CANDIDATE_SQL} DESC, "
        "(p.posted_at IS NULL), p.posted_at DESC, p.first_seen DESC, p.posting_id"
    )
    if limit is not None or offset:
        sql += " LIMIT ? OFFSET ?"
        params += [-1 if limit is None else limit, offset]
    return [dict(r) for r in conn.execute(sql, params)]


def dismiss(conn: sqlite3.Connection, posting_id: str, reason: str, now: str) -> None:
    """One-click "not for me": hides the posting and records the negative label.

    Exactly the old no_match plumbing — `verdict='no_match'` IS the dismissed flag,
    so history and new dismissals train identically. Undo via `undo_dismiss`.
    """
    set_verdict(conn, posting_id, "no_match", reason, now)


def undo_dismiss(conn: sqlite3.Connection, posting_id: str) -> None:
    """Put a dismissed posting back in the Inbox as if never touched."""
    reset_verdict(conn, posting_id)


def pin_tier(conn: sqlite3.Connection, posting_id: str, tier: str, now: str) -> None:
    """Legacy per-posting pin API retained for old CLI callers and training history.

    Pins no longer override Board company groups. Records the previous legacy pin or
    company group as `tier_before_pin` for the historical ranking signal.
    """
    if tier not in tiers.TIERS:
        raise ValueError(f"tier must be one of {tiers.TIERS}, got {tier!r}")
    row = conn.execute(
        "SELECT COALESCE(pinned_tier, tier) AS effective FROM postings WHERE posting_id = ?",
        (posting_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown posting {posting_id!r}")
    conn.execute(
        "UPDATE postings SET pinned_tier = ?, tier_before_pin = ?, pinned_at = ? "
        "WHERE posting_id = ?",
        (tier, row["effective"], now, posting_id),
    )
    conn.commit()


def unpin(conn: sqlite3.Connection, posting_id: str) -> None:
    """Release a pin: the posting follows its computed tier again (and the pin's
    training signal disappears with it)."""
    conn.execute(
        "UPDATE postings SET pinned_tier = NULL, tier_before_pin = NULL, pinned_at = NULL "
        "WHERE posting_id = ?",
        (posting_id,),
    )
    conn.commit()


FLAG_REASON_DEFAULT = "flagged: check this posting"


def flag(conn: sqlite3.Connection, posting_id: str, reason: str, now: str) -> None:
    """Park a posting in the flagged-for-review queue (suspect data, e.g. dead link).

    Orthogonal to dismiss: the verdict is untouched and NO ranking label is created —
    bad data is not a preference signal. Hidden from the Inbox until `unflag` (or a
    dismiss, if the investigation confirms the posting is gone).
    """
    cur = conn.execute(
        "UPDATE postings SET flagged_at = ?, flag_reason = ? WHERE posting_id = ?",
        (now, reason or FLAG_REASON_DEFAULT, posting_id),
    )
    if cur.rowcount == 0:
        raise ValueError(f"unknown posting {posting_id!r}")
    conn.commit()


def unflag(conn: sqlite3.Connection, posting_id: str) -> None:
    """Restore a flagged posting to the Inbox (the flag was a false alarm)."""
    conn.execute(
        "UPDATE postings SET flagged_at = NULL, flag_reason = NULL WHERE posting_id = ?",
        (posting_id,),
    )
    conn.commit()


def list_flagged(conn: sqlite3.Connection) -> list[dict]:
    """The flagged queue for the investigate-flags skill, newest flag first.

    Excludes dismissed rows: a flag resolved as "confirmed gone" is off the queue
    (and comes back if the dismissal is undone).
    """
    return [
        dict(r)
        for r in conn.execute(
            "SELECT posting_id, source_key, company, title, url, payload_path, "
            "is_active, flag_reason, flagged_at FROM postings "
            f"WHERE {store.FLAGGED_SQL} ORDER BY flagged_at DESC, posting_id"
        )
    ]


def refresh_ranking(conn: sqlite3.Connection) -> None:
    """Best-effort rescore + retier after an action — never blocks the action itself."""
    try:
        from internshelper import ranking

        ranking.rescore_inbox(conn, now=clock.now_iso())
        tiers.retier_inbox(conn, tiers.load_tier_map())
    except Exception:
        pass


def payload_summary(payload_path: str | None, *, max_chars: int = 2000) -> dict:
    """Human-readable summary of an archived raw payload, for judging a pending posting.

    Returns {"state": "missing"|"unreadable"|"ok", "description": str, "raw": object|None}.
    The description is the first present key in `_DESCRIPTION_KEYS`, rendered to readable
    plain text (paragraphs/bullets kept) and truncated to `max_chars`; `raw` is the full
    parsed payload (nothing hidden).
    """
    if not payload_path:
        return {"state": "missing", "description": "", "raw": None}
    try:
        raw = json.loads(Path(payload_path).read_text(encoding="utf-8"))
    except Exception:
        return {"state": "unreadable", "description": "", "raw": None}
    desc = ""
    if isinstance(raw, dict):
        for k in _DESCRIPTION_KEYS:
            if raw.get(k):
                desc = text.html_to_text(str(raw[k]))
                if len(desc) > max_chars:
                    desc = desc[:max_chars] + "…"
                break
    return {"state": "ok", "description": desc, "raw": raw}


def applied(conn: sqlite3.Connection) -> list[dict]:
    """Posting_ids the user has an application row for, with names when the posting still exists.

    Left join so an application whose posting was pruned is still reported — the deep-scan-source
    guard must skip it either way. `company`/`title` are None for a missing posting row.
    """
    return [
        dict(r)
        for r in conn.execute(
            "SELECT a.posting_id, p.company, p.title, a.status, a.applied_date "
            "FROM applications a LEFT JOIN postings p ON p.posting_id = a.posting_id "
            "ORDER BY a.posting_id"
        )
    ]


def _open() -> sqlite3.Connection:
    conn = db.connect(config.default_path("INTERNSHELPER_DB", "data/internshelper.db"))
    db.init_db(conn)
    return conn


def main(argv=None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="internshelper.review")
    sub = parser.add_subparsers(dest="cmd", required=True)

    li = sub.add_parser("list-inbox", help="inbox postings, best-first (JSON)")
    li.add_argument("--tier", choices=tiers.TIERS, default=None)
    li.add_argument("--limit", type=int, default=None)

    dm = sub.add_parser("dismiss", help="hide one posting ('not for me'; negative label)")
    dm.add_argument("posting_id")
    dm.add_argument("--reason", default=DISMISS_REASON)

    ud = sub.add_parser("undo-dismiss", help="return a dismissed posting to the inbox")
    ud.add_argument("posting_id")

    pn = sub.add_parser("pin", help="pin a posting to a tier (sticky + training signal)")
    pn.add_argument("posting_id")
    pn.add_argument("--tier", required=True, choices=tiers.TIERS)

    up = sub.add_parser("unpin", help="release a pin — the computed tier applies again")
    up.add_argument("posting_id")

    fl = sub.add_parser("flag", help="flag one posting for review (suspect data; no ranking label)")
    fl.add_argument("posting_id")
    fl.add_argument("--reason", default=FLAG_REASON_DEFAULT)

    uf = sub.add_parser("unflag", help="restore a flagged posting to the inbox")
    uf.add_argument("posting_id")

    sub.add_parser("list-flagged", help="the flagged-for-review queue (JSON)")

    sub.add_parser("applied", help="posting_ids the user has applied to (JSON) — deep-scan guard")
    sub.add_parser("list-leaks", help="inbox rows failing their source's current title guard (JSON)")
    sub.add_parser("clear-leaks", help="bulk-dismiss every guard leak (undoable batch)")

    args = parser.parse_args(argv)
    conn = _open()

    if args.cmd in ("list-leaks", "clear-leaks"):
        entries, errors = config.load_sources(
            config.default_path("INTERNSHELPER_SOURCES", "config/sources.yaml")
        )
        for e in errors:
            print(f"config warning: {e}", file=sys.stderr)
        if args.cmd == "list-leaks":
            print(json.dumps(find_guard_leaks(conn, entries), indent=2))
        else:
            n = clear_guard_leaks(conn, entries, now=clock.now_iso())
            refresh_ranking(conn)
            print(f"cleared {n} guard leak(s)")
    elif args.cmd == "list-inbox":
        print(json.dumps(list_inbox(conn, args.limit, tier=args.tier), indent=2))
    elif args.cmd == "dismiss":
        dismiss(conn, args.posting_id, args.reason, now=clock.now_iso())
        refresh_ranking(conn)  # every action is a new label
        print(f"{args.posting_id}: dismissed")
    elif args.cmd == "undo-dismiss":
        undo_dismiss(conn, args.posting_id)
        refresh_ranking(conn)
        print(f"{args.posting_id}: back in the inbox")
    elif args.cmd == "pin":
        pin_tier(conn, args.posting_id, args.tier, now=clock.now_iso())
        refresh_ranking(conn)
        print(f"{args.posting_id}: pinned to {args.tier}")
    elif args.cmd == "unpin":
        unpin(conn, args.posting_id)
        refresh_ranking(conn)
        print(f"{args.posting_id}: unpinned")
    elif args.cmd == "flag":
        flag(conn, args.posting_id, args.reason, now=clock.now_iso())
        refresh_ranking(conn)  # flagged rows leave the inbox scope
        print(f"{args.posting_id}: flagged for review")
    elif args.cmd == "unflag":
        unflag(conn, args.posting_id)
        refresh_ranking(conn)
        print(f"{args.posting_id}: back in the inbox")
    elif args.cmd == "list-flagged":
        print(json.dumps(list_flagged(conn), indent=2))
    elif args.cmd == "applied":
        print(json.dumps(applied(conn), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
