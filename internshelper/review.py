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

# Payload keys tried (in order) for a human-readable description, across connector shapes.
_DESCRIPTION_KEYS = ("content", "description", "descriptionPlain", "descriptionHtml", "plain")

_FIELDS = (
    "posting_id, title, company, location, url, payload_path, posted_at, first_seen, "
    "is_internship, is_newgrad, is_cs_relevant"
)


def list_pending(
    conn: sqlite3.Connection, limit: int | None = None, offset: int = 0
) -> list[dict]:
    """Pending postings: keyword-candidates first, then most-recently-posted first.

    Within a candidate tier, rows with a known `posted_at` come before NULLs, newest first,
    then `first_seen` as a tiebreak — so the freshest roles surface at the top. The final
    `posting_id` tiebreak makes `limit`/`offset` paging stable.
    """
    sql = (
        f"SELECT {_FIELDS} FROM postings WHERE review_status = 'pending' "
        f"ORDER BY {store.CANDIDATE_SQL} DESC, "
        "(posted_at IS NULL), posted_at DESC, first_seen DESC, posting_id"
    )
    params: list[object] = []
    if limit is not None or offset:
        sql += " LIMIT ? OFFSET ?"
        params += [-1 if limit is None else limit, offset]
    return [dict(r) for r in conn.execute(sql, params)]


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


def undo_bulk_clear(conn: sqlite3.Connection, reviewed_at: str, reason: str) -> int:
    """Revert one bulk-clear batch (matched by its shared stamp + reason) to pending."""
    cur = conn.execute(
        "UPDATE postings SET verdict = NULL, verdict_reason = NULL, reviewed_at = NULL, "
        "review_status = 'pending' "
        "WHERE verdict = 'no_match' AND reviewed_at = ? AND verdict_reason = ?",
        (reviewed_at, reason),
    )
    conn.commit()
    return cur.rowcount


def payload_summary(payload_path: str | None, *, max_chars: int = 2000) -> dict:
    """Human-readable summary of an archived raw payload, for judging a pending posting.

    Returns {"state": "missing"|"unreadable"|"ok", "description": str, "raw": object|None}.
    The description is the first present key in `_DESCRIPTION_KEYS`, HTML-stripped and
    truncated to `max_chars`; `raw` is the full parsed payload (nothing hidden).
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
                desc = text.strip_html(str(raw[k]))[:max_chars]
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

    args = parser.parse_args(argv)
    conn = _open()

    if args.cmd == "list-pending":
        print(json.dumps(list_pending(conn, args.limit), indent=2))
    elif args.cmd == "set-verdict":
        set_verdict(conn, args.posting_id, args.verdict, args.reason, now=clock.now_iso())
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
