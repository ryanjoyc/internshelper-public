"""SQLite connection, schema bootstrap, meta key/value, and runs pruning.

All timestamps are UTC ISO-8601 strings (see internshelper.clock), so string
comparison is chronological.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    posting_id      TEXT PRIMARY KEY,
    source_key      TEXT NOT NULL,
    source_type     TEXT NOT NULL,
    title           TEXT NOT NULL,
    company         TEXT,
    location        TEXT,
    url             TEXT,
    description     TEXT,
    is_internship   INTEGER NOT NULL DEFAULT 0,
    is_newgrad      INTEGER NOT NULL DEFAULT 0,
    is_cs_relevant  INTEGER NOT NULL DEFAULT 0,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1,
    -- v2: decoupled collect-then-review
    payload_path    TEXT,
    review_status   TEXT NOT NULL DEFAULT 'pending',
    verdict         TEXT,
    verdict_reason  TEXT,
    reviewed_at     TEXT,
    -- v2.3: source's own posted/added date (recency); NULL if the source gives none
    posted_at       TEXT
);

CREATE TABLE IF NOT EXISTS applications (
    posting_id      TEXT PRIMARY KEY,
    status          TEXT,
    notes           TEXT,
    applied_date    TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    source_key      TEXT NOT NULL,
    ok              INTEGER NOT NULL,
    count           INTEGER NOT NULL DEFAULT 0,
    dropped         INTEGER NOT NULL DEFAULT 0,
    error           TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key             TEXT PRIMARY KEY,
    value           TEXT
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a connection with WAL + a 5s busy timeout (single-user concurrency)."""
    path = Path(path)
    if path.parent != Path(""):
        path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: Streamlit reruns the app on different threads, so a
    # connection may be created on one thread and used on another. Safe here — access is
    # serialized (single-user, one script run at a time) and guarded by WAL + busy_timeout.
    conn = sqlite3.connect(str(path), timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# v2 columns added to `postings`, as (name, column definition) for additive migration of
# pre-v2 databases. ADD COLUMN with a constant DEFAULT is allowed by SQLite.
_POSTINGS_V2_COLUMNS = [
    ("payload_path", "TEXT"),
    ("review_status", "TEXT NOT NULL DEFAULT 'pending'"),
    ("verdict", "TEXT"),
    ("verdict_reason", "TEXT"),
    ("reviewed_at", "TEXT"),
    ("posted_at", "TEXT"),
]

# Additive migration for the `runs` table, same idiom as `_POSTINGS_V2_COLUMNS`.
_RUNS_V2_COLUMNS = [
    ("dropped", "INTEGER NOT NULL DEFAULT 0"),
]


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables if they do not exist, then apply additive migrations (idempotent)."""
    conn.executescript(SCHEMA)
    _migrate_postings(conn)
    _migrate_runs(conn)
    conn.commit()


def _migrate_postings(conn: sqlite3.Connection) -> None:
    """Add any v2 `postings` columns missing from a pre-v2 database, preserving data."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(postings)")}
    for name, decl in _POSTINGS_V2_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE postings ADD COLUMN {name} {decl}")


def _migrate_runs(conn: sqlite3.Connection) -> None:
    """Add any newer `runs` columns missing from an older database, preserving data."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    for name, decl in _RUNS_V2_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {decl}")


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def prune_runs(conn: sqlite3.Connection, now: str, days: int = 30) -> int:
    """Delete runs strictly older than `days` before `now` (a UTC ISO-8601 string).

    Returns the number of rows deleted.
    """
    cutoff = (datetime.fromisoformat(now) - timedelta(days=days)).isoformat()
    cur = conn.execute("DELETE FROM runs WHERE started_at < ?", (cutoff,))
    conn.commit()
    return cur.rowcount
