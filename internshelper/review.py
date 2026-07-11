"""On-demand review hooks — the testable CLI the `/review-internships` skill drives.

The cron only collects; the agent (in a Claude Code session) is the classifier. It calls
`list-pending` to get the queue (keyword-candidates first), reads each payload, and calls
`set-verdict` per posting, then `finish`. `summary` prints the confirmed shortlist.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from internshelper import clock, config, db, store, text
from internshelper.dotenv import load_dotenv

VERDICTS = ("match", "no_match")

BULK_CLEAR_REASON = "bulk: non-candidate"
GUARD_LEAK_REASON = "bulk: guard leak"
CLOSED_REASON = "bulk: closed on source"
# Tier bulk verdicts keep the "bulk:" prefix on purpose: they echo the model's own
# predictions, so as training labels they must stay weak (see ranking._WEAK_PREFIXES) —
# the ranker never feasts on its own outputs at full weight.
TIER_ACCEPT_REASON = "bulk: tier accept"
TIER_DISMISS_REASON = "bulk: tier dismiss"

# Payload keys tried (in order) for a human-readable description, across connector shapes.
_DESCRIPTION_KEYS = ("content", "description", "descriptionPlain", "descriptionHtml", "plain")

_FIELDS = (
    "posting_id, source_key, title, company, location, url, payload_path, posted_at, "
    "first_seen, is_internship, is_newgrad, is_cs_relevant, rank_score, rank_reasons"
)

SORTS = ("rank", "newest")

_RECENCY_SQL = "(posted_at IS NULL), posted_at DESC, first_seen DESC, posting_id"

# Best-first: learned rank_score dominates when present (NULL = unscored/cold-start,
# sorts after scored rows); the keyword-candidate tier + recency remain the tiebreak
# and are the entire order when every score is NULL — byte-for-byte the pre-ranking
# behavior. Ranking orders, never gates: no WHERE clause depends on the score.
_RANK_SQL = "(rank_score IS NULL), rank_score DESC"


def _pending_where(q: str = "", source: str = "") -> tuple[str, list[object]]:
    clauses = ["review_status = 'pending'"]
    params: list[object] = []
    if q:
        clauses.append("(LOWER(title) LIKE ? OR LOWER(company) LIKE ?)")
        like = f"%{q.lower()}%"
        params += [like, like]
    if source:
        clauses.append("source_key = ?")
        params.append(source)
    return " WHERE " + " AND ".join(clauses), params


def list_pending(
    conn: sqlite3.Connection,
    limit: int | None = None,
    offset: int = 0,
    *,
    q: str = "",
    source: str = "",
    sort: str = "rank",
) -> list[dict]:
    """Pending postings; `sort="rank"` = best-first (learned score when trained,
    keyword-candidates-first as tiebreak/fallback), `sort="newest"` = pure recency.

    Optional `q` searches title/company (case-insensitive substring); `source` filters
    on an exact source_key. Within a tier, rows with a known `posted_at` come before
    NULLs, newest first, then `first_seen` — so the freshest roles surface at the top.
    The final `posting_id` tiebreak makes `limit`/`offset` paging stable.
    """
    where, params = _pending_where(q, source)
    order = (
        _RECENCY_SQL
        if sort == "newest"
        else f"{_RANK_SQL}, {store.CANDIDATE_SQL} DESC, {_RECENCY_SQL}"
    )
    sql = f"SELECT {_FIELDS} FROM postings{where} ORDER BY {order}"
    if limit is not None or offset:
        sql += " LIMIT ? OFFSET ?"
        params += [-1 if limit is None else limit, offset]
    return [dict(r) for r in conn.execute(sql, params)]


def count_pending_filtered(conn: sqlite3.Connection, *, q: str = "", source: str = "") -> int:
    where, params = _pending_where(q, source)
    return conn.execute(f"SELECT COUNT(*) FROM postings{where}", params).fetchone()[0]


def partition_tiers(rows: list[dict], *, high: float, low: float) -> list[dict]:
    """Split pending rows into the three review tiers by learned score.

    `>= high` is "near-certain", `< low` is "probably not", everything between
    "needs your eyes". Unscored (cold-start) rows fall back to the keyword-candidate
    heuristic: candidates need eyes, non-candidates are probably-not. Rows keep the
    caller's order and gain a `tier` key; each tier's `bulk_count` counts only its
    SCORED rows — the bulk verdicts never act on the heuristic alone.
    """
    buckets: dict[str, list[dict]] = {"high": [], "mid": [], "low": []}
    for row in rows:
        score = row.get("rank_score")
        if score is None:
            key = "mid" if store.is_candidate(row) else "low"
        elif score >= high:
            key = "high"
        elif score < low:
            key = "low"
        else:
            key = "mid"
        row["tier"] = key
        buckets[key].append(row)
    return [
        {"key": "high", "rows": buckets["high"], "bulk_count": len(buckets["high"])},
        {"key": "mid", "rows": buckets["mid"], "bulk_count": 0},
        {
            "key": "low",
            "rows": buckets["low"],
            "bulk_count": sum(1 for r in buckets["low"] if r.get("rank_score") is not None),
        },
    ]


def tier_counts(
    conn: sqlite3.Connection, *, high: float, low: float, q: str = "", source: str = ""
) -> dict:
    """Pending-queue size per review tier (same bucketing rule as partition_tiers)."""
    where, params = _pending_where(q, source)
    row = conn.execute(
        "SELECT "
        "COALESCE(SUM(rank_score >= ?), 0), "
        "COALESCE(SUM(rank_score >= ? AND rank_score < ?), 0) "
        f"  + COALESCE(SUM(rank_score IS NULL AND {store.CANDIDATE_SQL}), 0), "
        "COALESCE(SUM(rank_score < ?), 0) "
        f"  + COALESCE(SUM(rank_score IS NULL AND NOT {store.CANDIDATE_SQL}), 0) "
        f"FROM postings{where}",
        (high, low, high, low, *params),
    ).fetchone()
    return {"high": row[0], "mid": row[1], "low": row[2]}


def accept_high_tier(
    conn: sqlite3.Connection, now: str, *, threshold: float, q: str = "", source: str = ""
) -> int:
    """Bulk-match every scored pending row at/above `threshold`, scoped by the filters.

    One undoable batch (shared stamp + TIER_ACCEPT_REASON — revert via
    `undo_bulk_clear`). Callers must still call `finish()` afterwards (same
    contract as every verdict path).
    """
    where, params = _pending_where(q, source)
    cur = conn.execute(
        "UPDATE postings SET verdict = 'match', verdict_reason = ?, reviewed_at = ?, "
        f"review_status = 'reviewed'{where} AND rank_score >= ?",
        (TIER_ACCEPT_REASON, now, *params, threshold),
    )
    conn.commit()
    return cur.rowcount


def dismiss_low_tier(
    conn: sqlite3.Connection, now: str, *, threshold: float, q: str = "", source: str = ""
) -> int:
    """Bulk no_match every scored pending row below `threshold`, scoped by the filters.

    NULL never satisfies `rank_score < ?`, so cold-start rows are untouched — only
    the model's own "probably not" verdicts are dismissed. Undo via `undo_bulk_clear`.
    """
    where, params = _pending_where(q, source)
    cur = conn.execute(
        "UPDATE postings SET verdict = 'no_match', verdict_reason = ?, reviewed_at = ?, "
        f"review_status = 'reviewed'{where} AND rank_score < ?",
        (TIER_DISMISS_REASON, now, *params, threshold),
    )
    conn.commit()
    return cur.rowcount


def pending_source_keys(conn: sqlite3.Connection) -> list[str]:
    """Distinct source_keys among pending rows — the Review source-filter options."""
    return [
        r["source_key"]
        for r in conn.execute(
            "SELECT DISTINCT source_key FROM postings WHERE review_status = 'pending' "
            "ORDER BY source_key"
        )
    ]


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


def clear_non_candidate_pending(
    conn: sqlite3.Connection, now: str, reason: str = BULK_CLEAR_REASON
) -> int:
    """Mark every non-candidate pending posting no_match in one statement.

    The shared `now` stamp + `reason` identify the batch, so `undo_bulk_clear` can
    revert exactly these rows. Returns the number cleared. Callers must still call
    `finish()` afterwards (same contract as every verdict path).
    """
    cur = conn.execute(
        "UPDATE postings SET verdict = 'no_match', verdict_reason = ?, reviewed_at = ?, "
        f"review_status = 'reviewed' WHERE review_status = 'pending' AND NOT {store.CANDIDATE_SQL}",
        (reason, now),
    )
    conn.commit()
    return cur.rowcount


def _applied_ids(conn: sqlite3.Connection) -> set[str]:
    return {r["posting_id"] for r in conn.execute("SELECT posting_id FROM applications")}


def find_guard_leaks(conn: sqlite3.Connection, entries) -> list[dict]:
    """Pending rows whose title no longer passes their source's CURRENT title guard.

    Catches postings collected before a guard was fixed/tightened (e.g. the substring-era
    'Internal…' leaks). Never flags: sources absent from the loaded config (removed),
    guardless sources (accepts() is trivially True), or posting_ids the user applied to.
    """
    by_key = {e.source_key: e for e in entries}
    applied_ids = _applied_ids(conn)
    rows = conn.execute(
        "SELECT posting_id, title, company, source_key FROM postings "
        "WHERE review_status = 'pending' ORDER BY source_key, posting_id"
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
    """Mark the given pending posting_ids no_match as one undoable batch (stamp + reason)."""
    if not ids:
        return 0
    ph = ",".join("?" * len(ids))
    cur = conn.execute(
        "UPDATE postings SET verdict = 'no_match', verdict_reason = ?, reviewed_at = ?, "
        f"review_status = 'reviewed' WHERE review_status = 'pending' AND posting_id IN ({ph})",
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


def find_closed_pending(conn: sqlite3.Connection) -> list[dict]:
    """Pending rows whose posting vanished from its source board (close-detection).

    They can't be applied to anymore, yet they linger in the queue because
    `list_pending` ignores `is_active`. Applied posting_ids are never flagged.
    """
    applied_ids = _applied_ids(conn)
    rows = conn.execute(
        "SELECT posting_id, title, company, source_key FROM postings "
        "WHERE review_status = 'pending' AND is_active = 0 ORDER BY source_key, posting_id"
    )
    return [dict(r) for r in rows if r["posting_id"] not in applied_ids]


def clear_closed_pending(
    conn: sqlite3.Connection, now: str, reason: str = CLOSED_REASON
) -> int:
    """Bulk no_match every closed-but-pending row. Undo via `undo_bulk_clear`."""
    ids = [r["posting_id"] for r in find_closed_pending(conn)]
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


def finish(conn: sqlite3.Connection) -> bool:
    """If no postings remain pending, clear the notify flag so the next batch can nudge again.

    Returns True if the flag was reset.
    """
    pending = conn.execute(
        "SELECT COUNT(*) FROM postings WHERE review_status = 'pending'"
    ).fetchone()[0]
    if pending == 0:
        db.set_meta(conn, "pending_notified", "0")
        return True
    return False


def summary(conn: sqlite3.Connection) -> list[dict]:
    """Confirmed matches (verdict = 'match'), newest reviewed first."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT posting_id, title, company, location, url, verdict_reason, reviewed_at, "
            "posted_at, first_seen "
            "FROM postings WHERE verdict = 'match' ORDER BY reviewed_at DESC, posting_id"
        )
    ]


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

    lp = sub.add_parser("list-pending", help="pending postings, candidates first (JSON)")
    lp.add_argument("--limit", type=int, default=None)

    sv = sub.add_parser("set-verdict", help="record a verdict for one posting")
    sv.add_argument("posting_id")
    sv.add_argument("--verdict", required=True, choices=VERDICTS)
    sv.add_argument("--reason", default="")

    sub.add_parser("finish", help="reset the notify flag if the queue is empty")
    sub.add_parser("summary", help="confirmed matches (JSON)")
    sub.add_parser("applied", help="posting_ids the user has applied to (JSON) — deep-scan guard")
    sub.add_parser("list-leaks", help="pending rows failing their source's current title guard (JSON)")
    sub.add_parser("clear-leaks", help="bulk no_match every guard leak (undoable batch)")

    args = parser.parse_args(argv)
    conn = _open()

    if args.cmd == "list-pending":
        print(json.dumps(list_pending(conn, args.limit), indent=2))
    elif args.cmd in ("list-leaks", "clear-leaks"):
        entries, errors = config.load_sources(
            config.default_path("INTERNSHELPER_SOURCES", "config/sources.yaml")
        )
        for e in errors:
            print(f"config warning: {e}", file=sys.stderr)
        if args.cmd == "list-leaks":
            print(json.dumps(find_guard_leaks(conn, entries), indent=2))
        else:
            n = clear_guard_leaks(conn, entries, now=clock.now_iso())
            finish(conn)
            print(f"cleared {n} guard leak(s)")
    elif args.cmd == "set-verdict":
        set_verdict(conn, args.posting_id, args.verdict, args.reason, now=clock.now_iso())
        try:  # every verdict is a new label — retrain the ranker, but never block on it
            from internshelper import ranking

            ranking.rescore_pending(conn, now=clock.now_iso())
        except Exception:
            pass
        print(f"{args.posting_id}: {args.verdict}")
    elif args.cmd == "finish":
        reset = finish(conn)
        print("notify flag reset" if reset else "still pending — not reset")
    elif args.cmd == "summary":
        print(json.dumps(summary(conn), indent=2))
    elif args.cmd == "applied":
        print(json.dumps(applied(conn), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
