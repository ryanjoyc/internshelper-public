"""Inbox actions — the testable layer used by Board routes and posting-audit skills.

There is no approval gate: every collected posting is in the Inbox, and curation is
dismiss (hide + negative label), flag (park suspect data for investigation — hidden,
NO label), application movement, duplicate review, and bulk hygiene sweeps. Company
groups are managed separately. The legacy pin/verdict primitives remain for schema and
CLI compatibility; dismiss is built on the verdict fields.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from internshelper import clock, companygroups, config, db, store, text, tiers
from internshelper.dotenv import load_dotenv

VERDICTS = ("match", "no_match")

DISMISS_REASON = "dismissed: board"

GUARD_LEAK_REASON = "bulk: guard leak"
CLOSED_REASON = "bulk: closed on source"

# Payload keys tried (in order) for a human-readable description, across connector shapes.
_DESCRIPTION_KEYS = ("content", "description", "descriptionPlain", "descriptionHtml", "plain")


def company_group_activity(
    conn: sqlite3.Connection,
    entries: list[companygroups.CompanyGroupEntry],
) -> tuple[dict[str, int], dict[str, int]]:
    """Count companies and postings in the Board's company-grouped Inbox."""
    mapping = companygroups.group_map(entries)
    companies_by_group = {key: set() for key in companygroups.ALL_GROUPS}
    posting_counts: dict[str, int] = {}
    for row in store.inbox_with_status(conn):
        if row["status"] in store.PIPELINE_STATUSES:
            continue
        raw_name = (row["company"] or "").strip()
        group, entry = companygroups.classify(raw_name, mapping)
        display_name = entry.name if entry else (raw_name or "Unknown")
        identity = companygroups.normalize_company(display_name) or "unknown"
        companies_by_group[group].add(identity)
        posting_counts[display_name] = posting_counts.get(display_name, 0) + 1
    return (
        {key: len(names) for key, names in companies_by_group.items()},
        posting_counts,
    )


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
    """Clear the legacy verdict fields so the posting returns to the Inbox."""
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
    """Legacy Inbox rows whose posting vanished from its source board.

    Availability-managed rows have their own evidence-backed Board projection and are
    deliberately excluded: sweeping one into ``verdict='no_match'`` would turn an
    availability outcome into ranker training data. Applied posting_ids are never
    flagged.
    """
    applied_ids = _applied_ids(conn)
    rows = conn.execute(
        "SELECT p.posting_id, p.title, p.company, p.source_key FROM postings p "
        "LEFT JOIN availability_state av ON av.posting_id = p.posting_id "
        f"WHERE {store.INBOX_SQL} AND p.is_active = 0 "
        "AND av.posting_id IS NULL ORDER BY p.source_key, p.posting_id"
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
    needle = q.lower()
    rows = [
        dict(row)
        for row in store.inbox_with_status(conn)
        if row["status"] not in store.PIPELINE_STATUSES
        and (
            not needle
            or needle in (row["title"] or "").lower()
            or needle in (row["company"] or "").lower()
        )
        and (not source or row["source_key"] == source)
        and (not tier or row["tier"] == tier)
    ]
    end = None if limit is None else offset + limit
    return rows[offset:end]


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


def confirm_duplicates(
    conn: sqlite3.Connection, survivor_id: str, dup_ids: list[str]
) -> int:
    """Mark `dup_ids` as duplicates of `survivor_id` (hidden from the Inbox, reversible).

    Orthogonal to dismiss/flag: no ranking label — a duplicate is not a preference signal.
    The survivor is never pointed at itself. Returns the number of rows hidden.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        ids, expected_survivor, merge_blocked = _validate_possible_duplicate_group(
            conn, dup_ids
        )
        if merge_blocked:
            raise ValueError(
                "multiple saved application records cannot be merged safely"
            )
        if survivor_id != expected_survivor:
            raise ValueError("duplicate survivor no longer matches the live suggestion")
        targets = [pid for pid in ids if pid != survivor_id]
        marks = ",".join("?" for _ in targets)
        cur = conn.execute(
            f"UPDATE postings SET duplicate_of = ? "
            f"WHERE duplicate_of IS NULL AND posting_id IN ({marks})",
            (survivor_id, *targets),
        )
        if cur.rowcount != len(targets):
            raise ValueError("possible-duplicate group changed during confirmation")
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return cur.rowcount


def _validate_possible_duplicate_group(
    conn: sqlite3.Connection, posting_ids: list[str]
) -> tuple[list[str], str, bool]:
    """Resolve an exact live suggestion or reject stale/arbitrary client IDs."""
    from internshelper import dedup

    ids = list(dict.fromkeys(pid for pid in posting_ids if pid))
    requested = set(ids)
    if len(requested) < 2:
        raise ValueError("a possible-duplicate group requires at least two postings")
    for group in dedup.find_possible_duplicates(conn):
        live_ids = {row["posting_id"] for row in group["rows"]}
        if requested == live_ids:
            return ids, group["survivor_id"], group["merge_blocked"]
    raise ValueError("possible-duplicate group is stale or invalid")


def keep_separate(conn: sqlite3.Connection, posting_ids: list[str]) -> int:
    """Record a "these are distinct roles" decision so the fuzzy group stops resurfacing."""
    if not posting_ids:
        raise ValueError("a possible-duplicate group requires at least two postings")
    conn.execute("BEGIN IMMEDIATE")
    try:
        ids, _survivor, _merge_blocked = _validate_possible_duplicate_group(
            conn, posting_ids
        )
        marks = ",".join("?" for _ in ids)
        cur = conn.execute(
            f"UPDATE postings SET dedup_keep = 1 WHERE posting_id IN ({marks})",
            ids,
        )
        if cur.rowcount != len(ids):
            raise ValueError("possible-duplicate group changed during review")
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return cur.rowcount


def undo_duplicates(
    conn: sqlite3.Connection,
    posting_ids: list[str],
    *,
    survivor_id: str | None = None,
) -> int:
    """Un-merge rows, optionally only when they still point at the expected survivor."""
    ids = list(dict.fromkeys(pid for pid in posting_ids if pid))
    if not ids:
        return 0
    marks = ",".join("?" for _ in ids)
    if survivor_id:
        cur = conn.execute(
            f"UPDATE postings SET duplicate_of = NULL "
            f"WHERE duplicate_of = ? AND posting_id IN ({marks})",
            (survivor_id, *ids),
        )
    else:
        cur = conn.execute(
            f"UPDATE postings SET duplicate_of = NULL "
            f"WHERE duplicate_of IS NOT NULL AND posting_id IN ({marks})",
            ids,
        )
    conn.commit()
    return cur.rowcount


def undo_duplicate(conn: sqlite3.Connection, posting_id: str) -> None:
    """Backward-compatible single-row un-merge helper."""
    undo_duplicates(conn, [posting_id])


def undo_keep_separate(conn: sqlite3.Connection, posting_ids: list[str]) -> int:
    """Clear a reviewed-distinct decision so its fuzzy group can surface again."""
    ids = list(dict.fromkeys(pid for pid in posting_ids if pid))
    if not ids:
        return 0
    marks = ",".join("?" for _ in ids)
    cur = conn.execute(
        f"UPDATE postings SET dedup_keep = 0 "
        f"WHERE dedup_keep = 1 AND posting_id IN ({marks})",
        ids,
    )
    conn.commit()
    return cur.rowcount


def merged_duplicates(
    conn: sqlite3.Connection, limit: int = 200, offset: int = 0
) -> list[dict]:
    """Confirmed duplicates (the hidden losers), newest-survived first — the Merged view."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT posting_id, source_key, company, title, location, url, duplicate_of "
            "FROM postings WHERE duplicate_of IS NOT NULL "
            "ORDER BY company, title, posting_id LIMIT ? OFFSET ?",
            (limit, offset),
        )
    ]


def merged_duplicates_count(conn: sqlite3.Connection) -> int:
    """Number of currently hidden, human-confirmed duplicate rows."""
    return conn.execute(
        "SELECT COUNT(*) FROM postings WHERE duplicate_of IS NOT NULL"
    ).fetchone()[0]


def count_possible_duplicates(conn: sqlite3.Connection) -> int:
    """Number of company/title lookalike groups awaiting review (nav badge)."""
    from internshelper import dedup

    return len(dedup.find_possible_duplicates(conn))


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

    dd = sub.add_parser("dedup", help="cross-source de-duplication (URL auto-collapse / suggestions)")
    dd.add_argument("--run", action="store_true",
                    help="auto-collapse strong URL-match duplicates (writes)")
    dd.add_argument("--list", action="store_true",
                    help="list company/title lookalike groups awaiting review (JSON)")

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
    elif args.cmd == "dedup":
        from internshelper import dedup

        did = False
        if args.run:
            n = dedup.collapse_url_duplicates(conn)
            refresh_ranking(conn)
            print(f"collapsed {n} URL-match duplicate(s)")
            did = True
        if args.list or not did:
            print(json.dumps(dedup.find_possible_duplicates(conn), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
