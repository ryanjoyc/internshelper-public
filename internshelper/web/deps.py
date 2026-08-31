"""Per-request wiring: DB connections and the sidebar-nav context every page needs."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from ipaddress import ip_address
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status

from internshelper import db, store


def _origin(value: str) -> tuple[str, str, int] | None:
    """Return a normalized HTTP origin, or None for malformed/untrusted input."""
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").rstrip(".").lower()
        if parsed.scheme not in {"http", "https"} or not host:
            return None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None
    return parsed.scheme, host, port


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def require_local_browser_request(request: Request) -> None:
    """Reject unsafe browser requests that did not originate from this loopback app.

    The UI has no login because the server only binds to loopback. A hostile web page can
    still submit forms to localhost, and DNS rebinding can make a nonlocal Host appear
    same-origin. Require browser provenance and an exact local origin at the HTTP boundary.
    """
    target = _origin(str(request.url))
    supplied = request.headers.get("origin") or request.headers.get("referer")
    source = _origin(supplied) if supplied else None
    fetch_site = request.headers.get("sec-fetch-site")

    if (
        target is None
        or not _is_loopback_host(target[1])
        or source != target
        or fetch_site == "cross-site"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Source actions require a same-origin request from the local app.",
        )


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    # One connection per request, opened and closed on the same worker thread.
    # (db.connect keeps check_same_thread=False for the CLIs/cron sharing the file.)
    conn = db.connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


def nav_context(conn: sqlite3.Connection) -> dict:
    """Counts + status for the sidebar: inbox badge on Board, health dot."""
    runs = store.source_health(conn)
    if any(not r["ok"] for r in runs):
        dot = "err"
    elif any(r["quiet"] for r in runs):
        dot = "quiet"
    else:
        dot = "ok"
    return {
        "inbox": store.inbox_count(conn),
        "health_dot": dot,
        "last_collect": max((r["started_at"] for r in runs), default=None),
    }
