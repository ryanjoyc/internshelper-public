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

from internshelper import clock, config, db
from internshelper.dotenv import load_dotenv

VERDICTS = ("match", "no_match")

_FIELDS = (
    "posting_id, title, company, location, url, payload_path, posted_at, first_seen, "
    "is_internship, is_newgrad, is_cs_relevant"
)


def list_pending(conn: sqlite3.Connection, limit: int | None = None) -> list[dict]:
    """Pending postings: keyword-candidates first, then most-recently-posted first.

    Within a candidate tier, rows with a known `posted_at` come before NULLs, newest first,
    then `first_seen` as a tiebreak — so the freshest roles surface at the top.
    """
    sql = (
        f"SELECT {_FIELDS} FROM postings WHERE review_status = 'pending' "
        "ORDER BY (is_cs_relevant = 1 OR is_internship = 1 OR is_newgrad = 1) DESC, "
        "(posted_at IS NULL), posted_at DESC, first_seen DESC, posting_id"
    )
    params: list[object] = []
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
