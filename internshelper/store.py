"""Persistence: upsert postings, per-source close-detection, digest selection,
run logging, and application tracking. All timestamps are UTC ISO-8601 strings.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from internshelper.models import Posting

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

# Canonical application-status enum (was UI-only). `applications.status` is free text in
# the schema; this is the validated vocabulary every writer must use.
STATUS_OPTIONS = ("Untracked", "Interested", "Applied", "Interviewing", "Rejected", "Offer")

# The "keyword-candidate" rule, single source of truth. SQL fragment for queries;
# `is_candidate` is the same predicate for a fetched row (sqlite3.Row or dict).
CANDIDATE_SQL = "(is_cs_relevant = 1 OR is_internship = 1 OR is_newgrad = 1)"


def is_candidate(row) -> bool:
    return bool(row["is_cs_relevant"] or row["is_internship"] or row["is_newgrad"])


def payload_path_for(payloads_dir: str | Path, posting_id: str) -> Path:
    """Filesystem-safe path for a posting's raw payload file."""
    return Path(payloads_dir) / f"{_UNSAFE.sub('_', posting_id)}.json"


def upsert(
    conn: sqlite3.Connection,
    posting: Posting,
    now: str,
    payloads_dir: str | Path | None = None,
) -> None:
    """Insert a new posting, or update an existing one in place.

    On update, `first_seen` is preserved, `last_seen` is bumped to `now`, mutable fields
    are refreshed, and the row is reactivated (`is_active=1`). Review state
    (`review_status`, `verdict`, `payload_path`) is **never** touched by an update — only
    set on first insert.

    On first insert, if `payloads_dir` is given and `posting.raw` is present, the raw
    payload is written to disk and recorded as `payload_path`; `review_status` defaults
    to 'pending'.
    """
    is_new = conn.execute(
        "SELECT 1 FROM postings WHERE posting_id = ?", (posting.posting_id,)
    ).fetchone() is None

    payload_path = None
    if is_new and payloads_dir is not None and posting.raw is not None:
        dest = payload_path_for(payloads_dir, posting.posting_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(posting.raw, indent=2, default=str))
        payload_path = str(dest)

    conn.execute(
        """
        INSERT INTO postings (
            posting_id, source_key, source_type, title, company, location, url,
            description, is_internship, is_newgrad, is_cs_relevant,
            first_seen, last_seen, is_active, payload_path, posted_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)
        ON CONFLICT(posting_id) DO UPDATE SET
            source_key     = excluded.source_key,
            source_type    = excluded.source_type,
            title          = excluded.title,
            company        = excluded.company,
            location       = excluded.location,
            url            = excluded.url,
            description    = excluded.description,
            is_internship  = excluded.is_internship,
            is_newgrad     = excluded.is_newgrad,
            is_cs_relevant = excluded.is_cs_relevant,
            last_seen      = excluded.last_seen,
            posted_at      = excluded.posted_at,
            is_active      = 1
        """,
        (
            posting.posting_id, posting.source_key, posting.source_type, posting.title,
            posting.company, posting.location, posting.url, posting.description,
            int(posting.is_internship), int(posting.is_newgrad), int(posting.is_cs_relevant),
            now, now, payload_path, posting.posted_at,
        ),
    )
    conn.commit()


def apply_close_detection(
    conn: sqlite3.Connection,
    source_key: str,
    seen_ids: Iterable[str],
    ok: bool,
    count: int,
) -> int:
    """Mark this source's previously-active postings absent from `seen_ids` as closed.

    Gated on `ok AND count > 0`: an errored or empty fetch closes nothing (its postings
    retain their state), preventing false-closed churn. Scoped strictly to `source_key`.
    Returns the number of postings closed.
    """
    if not ok or count <= 0:
        return 0
    seen = list(seen_ids)
    if not seen:
        # No ids seen this fetch -> every active posting of this source is absent.
        # (`x NOT IN (NULL)` would yield UNKNOWN and close nothing — avoid it.)
        cur = conn.execute(
            "UPDATE postings SET is_active = 0 WHERE source_key = ? AND is_active = 1",
            (source_key,),
        )
    else:
        placeholders = ",".join("?" for _ in seen)
        cur = conn.execute(
            f"""
            UPDATE postings SET is_active = 0
            WHERE source_key = ? AND is_active = 1
              AND posting_id NOT IN ({placeholders})
            """,
            (source_key, *seen),
        )
    conn.commit()
    return cur.rowcount


def select_for_digest(
    conn: sqlite3.Connection,
    last_digest_at: str | None,
    require_cs: bool,
    require_intern_or_newgrad: bool,
) -> list[sqlite3.Row]:
    """Active postings first seen after the watermark that pass the filter, newest first."""
    clauses = ["is_active = 1"]
    params: list[object] = []
    if last_digest_at is not None:
        clauses.append("first_seen > ?")
        params.append(last_digest_at)
    if require_cs:
        clauses.append("is_cs_relevant = 1")
    if require_intern_or_newgrad:
        clauses.append("(is_internship = 1 OR is_newgrad = 1)")
    where = " AND ".join(clauses)
    return conn.execute(
        f"SELECT * FROM postings WHERE {where} ORDER BY first_seen DESC, posting_id",
        params,
    ).fetchall()


def _feed_where(
    require_cs: bool,
    require_intern_or_newgrad: bool,
    include_all: bool,
    include_closed: bool,
    search: str,
) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if not include_closed:
        clauses.append("is_active = 1")
    if not include_all:
        if require_cs:
            clauses.append("is_cs_relevant = 1")
        if require_intern_or_newgrad:
            clauses.append("(is_internship = 1 OR is_newgrad = 1)")
    if search:
        clauses.append("(LOWER(title) LIKE ? OR LOWER(company) LIKE ?)")
        like = f"%{search.lower()}%"
        params += [like, like]
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def feed(
    conn: sqlite3.Connection,
    *,
    require_cs: bool,
    require_intern_or_newgrad: bool,
    include_all: bool = False,
    include_closed: bool = False,
    search: str = "",
    limit: int | None = None,
    offset: int = 0,
) -> list[sqlite3.Row]:
    """Active postings for the Feed, newest first.

    `include_all` ignores the filter booleans (view everything); `include_closed` also
    shows archived/closed roles; `search` matches title or company (case-insensitive).
    `limit`/`offset` page the result (the ORDER BY's posting_id tiebreak keeps pages stable).
    """
    where, params = _feed_where(
        require_cs, require_intern_or_newgrad, include_all, include_closed, search
    )
    sql = f"SELECT * FROM postings{where} ORDER BY first_seen DESC, posting_id"
    if limit is not None or offset:
        sql += " LIMIT ? OFFSET ?"
        params += [-1 if limit is None else limit, offset]
    return conn.execute(sql, params).fetchall()


def feed_count(
    conn: sqlite3.Connection,
    *,
    require_cs: bool,
    require_intern_or_newgrad: bool,
    include_all: bool = False,
    include_closed: bool = False,
    search: str = "",
) -> int:
    """Total rows `feed` would return for the same filters (for pagination math)."""
    where, params = _feed_where(
        require_cs, require_intern_or_newgrad, include_all, include_closed, search
    )
    return conn.execute(f"SELECT COUNT(*) FROM postings{where}", params).fetchone()[0]


def tracker(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Postings that have an application row, with their status/notes/applied_date."""
    return conn.execute(
        """
        SELECT p.*, a.status, a.notes, a.applied_date
        FROM applications a
        JOIN postings p ON p.posting_id = a.posting_id
        ORDER BY a.status, p.company, p.title
        """
    ).fetchall()


def health(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """The latest run row per source_key (for the Health tab)."""
    return conn.execute(
        """
        SELECT r.* FROM runs r
        JOIN (SELECT source_key, MAX(id) AS mid FROM runs GROUP BY source_key) m
          ON r.id = m.mid
        ORDER BY r.source_key
        """
    ).fetchall()


# Synthetic run rows that aren't real sources (heartbeat / nudge / digest / config errors).
_META_SOURCE_KEYS = ("run", "notify", "config", "digest")


def source_baselines(conn: sqlite3.Connection, window: int = 5) -> dict[str, dict]:
    """Per real source, compare its latest fetch to a recent baseline ("went quiet").

    The baseline is the average `count` of up to `window` prior *successful* runs (excluding
    the latest). A source is flagged `quiet` when it had a real baseline (>0) and its latest
    successful run dropped to 0 OR sharply below baseline (<50%) — the classic "the board
    changed and we silently stopped seeing jobs" failure. Latest runs that errored are not
    flagged (they already render ❌ in Health). Keyed by source_key.
    """
    rows = conn.execute(
        "SELECT source_key, ok, count FROM runs "
        "WHERE source_key NOT IN (?,?,?,?) ORDER BY id DESC",
        _META_SOURCE_KEYS,
    ).fetchall()
    by_source: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_source.setdefault(r["source_key"], []).append(r)

    out: dict[str, dict] = {}
    for key, runs in by_source.items():
        latest = runs[0]
        prior_ok = [r["count"] for r in runs[1:] if r["ok"]][:window]
        baseline = sum(prior_ok) / len(prior_ok) if prior_ok else 0.0
        quiet = bool(
            latest["ok"] and baseline > 0
            and (latest["count"] == 0 or latest["count"] < 0.5 * baseline)
        )
        out[key] = {"baseline": baseline, "latest": latest["count"], "quiet": quiet}
    return out


def pending_counts(conn: sqlite3.Connection) -> tuple[int, int]:
    """(total pending, keyword-candidate pending) for the review queue + nudge."""
    total = conn.execute(
        "SELECT COUNT(*) FROM postings WHERE review_status = 'pending'"
    ).fetchone()[0]
    candidates = conn.execute(
        f"SELECT COUNT(*) FROM postings WHERE review_status = 'pending' AND {CANDIDATE_SQL}"
    ).fetchone()[0]
    return total, candidates


def record_run(
    conn: sqlite3.Connection,
    source_key: str,
    ok: bool,
    count: int,
    error: str | None,
    now: str,
    dropped: int = 0,
) -> None:
    conn.execute(
        "INSERT INTO runs (started_at, source_key, ok, count, dropped, error) "
        "VALUES (?,?,?,?,?,?)",
        (now, source_key, int(ok), count, dropped, error),
    )
    conn.commit()


def set_application(
    conn: sqlite3.Connection,
    posting_id: str,
    status: str | None = None,
    notes: str | None = None,
    applied_date: str | None = None,
) -> None:
    """Full upsert of an application row — sets all three columns.

    For a status-only change (e.g. a board drag) use `set_application_status`, which
    preserves existing notes/applied_date instead of overwriting them.
    """
    if status is not None and status not in STATUS_OPTIONS:
        raise ValueError(f"status must be one of {STATUS_OPTIONS}, got {status!r}")
    conn.execute(
        """
        INSERT INTO applications (posting_id, status, notes, applied_date)
        VALUES (?,?,?,?)
        ON CONFLICT(posting_id) DO UPDATE SET
            status = excluded.status,
            notes = excluded.notes,
            applied_date = excluded.applied_date
        """,
        (posting_id, status, notes, applied_date),
    )
    conn.commit()


def set_application_status(
    conn: sqlite3.Connection,
    posting_id: str,
    status: str,
    applied_date_if_empty: str | None = None,
) -> None:
    """Status-only upsert: existing notes/applied_date are left untouched.

    `applied_date_if_empty` fills the applied date ONLY when none is recorded yet
    (a board drag into Applied stamps today without clobbering a hand-set date).
    """
    if status not in STATUS_OPTIONS:
        raise ValueError(f"status must be one of {STATUS_OPTIONS}, got {status!r}")
    conn.execute(
        """
        INSERT INTO applications (posting_id, status, applied_date) VALUES (?,?,?)
        ON CONFLICT(posting_id) DO UPDATE SET
            status = excluded.status,
            applied_date = COALESCE(NULLIF(applications.applied_date, ''), excluded.applied_date)
        """,
        (posting_id, status, applied_date_if_empty),
    )
    conn.commit()


def get_application(conn: sqlite3.Connection, posting_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT status, notes, applied_date FROM applications WHERE posting_id = ?",
        (posting_id,),
    ).fetchone()


def matches_with_status(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Confirmed matches with their application state (NULL status = untracked), for the board."""
    return conn.execute(
        """
        SELECT p.*, a.status, a.notes, a.applied_date
        FROM postings p
        LEFT JOIN applications a ON a.posting_id = p.posting_id
        WHERE p.verdict = 'match'
        ORDER BY p.reviewed_at DESC, p.posting_id
        """
    ).fetchall()
