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
    posted_at       TEXT,
    -- v3: learned ranking (Phase 1.5) — persisted score + top contributing reasons (JSON).
    -- NULL rank_score = unscored / cold start; the queue then falls back to candidates-first.
    rank_score      REAL,
    rank_reasons    TEXT
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
    error           TEXT,
    -- titles dropped by a source's title_must_match flood guard before storing (visibility)
    dropped         INTEGER NOT NULL DEFAULT 0
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

# v3: learned ranking (Phase 1.5) — both nullable, so ADD COLUMN needs no default.
_POSTINGS_V3_COLUMNS = [
    ("rank_score", "REAL"),
    ("rank_reasons", "TEXT"),
]

# v4 legacy tiered-inbox columns. `tier` now stores the company group; pin fields remain for
# compatibility but do not control Board grouping. `notified_at` is the Top-target digest watermark.
_POSTINGS_V4_COLUMNS = [
    ("tier", "TEXT"),
    ("pinned_tier", "TEXT"),
    ("tier_before_pin", "TEXT"),
    ("pinned_at", "TEXT"),
    ("notified_at", "TEXT"),
]

# v5: flag-for-review — suspect data (dead link, wrong info) parked out of the Inbox
# for the investigate-flags skill. Orthogonal to verdict: a flag is not a preference
# signal and never trains the ranker. Flagged iff flagged_at IS NOT NULL.
_POSTINGS_V5_COLUMNS = [
    ("flagged_at", "TEXT"),
    ("flag_reason", "TEXT"),
]

# v6: cross-source de-duplication. `duplicate_of` points at the survivor posting this row
# duplicates (NULL = not a duplicate); a non-NULL row is hidden from the Inbox + digest but
# never deleted (reversible). `dedup_keep=1` means the user reviewed a fuzzy suggestion and
# said "keep separate" — suppresses re-suggesting that row. Strong URL matches auto-collapse;
# company/title lookalikes go to the human-confirmed Duplicates review lens. See dedup.py.
_POSTINGS_V6_COLUMNS = [
    ("duplicate_of", "TEXT"),
    ("dedup_keep", "INTEGER NOT NULL DEFAULT 0"),
]

# Columns added to `runs` after its original v2 shape, for additive migration of older DBs.
_RUNS_COLUMNS = [
    ("dropped", "INTEGER NOT NULL DEFAULT 0"),
]


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables if they do not exist, then apply additive migrations (idempotent)."""
    conn.executescript(SCHEMA)
    _migrate_columns(conn, "postings", _POSTINGS_V2_COLUMNS)
    _migrate_columns(conn, "postings", _POSTINGS_V3_COLUMNS)
    _migrate_columns(conn, "postings", _POSTINGS_V4_COLUMNS)
    _migrate_columns(conn, "postings", _POSTINGS_V5_COLUMNS)
    _migrate_columns(conn, "postings", _POSTINGS_V6_COLUMNS)
    _migrate_columns(conn, "runs", _RUNS_COLUMNS)
    conn.commit()
    _migrate_to_inbox(conn)


def _migrate_to_inbox(conn: sqlite3.Connection) -> None:
    """Historical one-time migration from the approval queue to the Inbox model.

    Stamps `notified_at` on every existing posting so the first digest only covers
    NEW arrivals (the Board shows the backlog — no first-run email blast; each row's
    own last_seen is a truthful, clock-free stamp). Dismissed/inbox state needs no
    migration: verdict='no_match' IS dismissed, everything else IS inbox. Also drops
    the old threshold-nudge flag.
    """
    if get_meta(conn, "inbox_migrated") == "1":
        return
    conn.execute(
        "UPDATE postings SET notified_at = last_seen WHERE notified_at IS NULL"
    )
    conn.execute("DELETE FROM meta WHERE key = 'pending_notified'")
    set_meta(conn, "inbox_migrated", "1")  # commits


def _migrate_columns(
    conn: sqlite3.Connection, table: str, columns: list[tuple[str, str]]
) -> None:
    """Add any listed columns missing from `table`, preserving existing data.

    SQLite allows ADD COLUMN with a constant DEFAULT, so this back-fills old databases
    without a rewrite. Idempotent: a column already present is skipped.
    """
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, decl in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


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
