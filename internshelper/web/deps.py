"""Per-request wiring: DB connections and the sidebar-nav context every page needs."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from fastapi import Request

from internshelper import db, store


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    # One connection per request, opened and closed on the same worker thread.
    # (db.connect keeps check_same_thread=False for the CLIs/cron sharing the file.)
    conn = db.connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


def nav_context(conn: sqlite3.Connection) -> dict:
    """Counts + status for the sidebar: pending badge, board badge, health dot."""
    total, candidates = store.pending_counts(conn)
    actionable = sum(
        1
        for r in store.matches_with_status(conn)
        if (r["status"] or "Untracked") in ("Untracked", "Interested")
    )
    runs = store.source_health(conn)
    if any(not r["ok"] for r in runs):
        dot = "err"
    elif any(r["quiet"] for r in runs):
        dot = "quiet"
    else:
        dot = "ok"
    return {
        "pending": total,
        "candidates": candidates,
        "board_actionable": actionable,
        "health_dot": dot,
        "last_collect": max((r["started_at"] for r in runs), default=None),
    }
