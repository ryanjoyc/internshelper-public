"""Persistence: upsert postings, per-source close-detection, run logging + health,
and application tracking. All timestamps are UTC ISO-8601 strings.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from internshelper.models import Posting

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


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


def feed(
    conn: sqlite3.Connection,
    *,
    require_cs: bool,
    require_intern_or_newgrad: bool,
    include_all: bool = False,
    include_closed: bool = False,
    search: str = "",
) -> list[sqlite3.Row]:
    """Active postings for the Feed, newest first.

    `include_all` ignores the filter booleans (view everything); `include_closed` also
    shows archived/closed roles; `search` matches title or company (case-insensitive).
    """
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
    return conn.execute(
        f"SELECT * FROM postings{where} ORDER BY first_seen DESC, posting_id", params
    ).fetchall()


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


def source_health(
    conn: sqlite3.Connection,
    *,
    window: int = 5,
    drop_ratio: float = 0.5,
    baseline_floor: int = 3,
) -> list[dict]:
    """Latest run per source (like `health`), enriched with a 'went quiet' signal.

    A real source (source_key shaped `type:token`) is flagged `quiet` when its most recent
    successful fetch collapsed versus its own recent baseline — either it returned 0 after a
    nonzero history (the classic "the board changed and we silently stopped seeing jobs"), or
    it fell far below the trailing average. Thresholds are parameters so the rule is testable:
      - `window`         — how many prior successful runs form the baseline.
      - `drop_ratio`     — "sharp drop" fires when latest < drop_ratio * baseline.
      - `baseline_floor` — ignore low-volume noise: no flag unless baseline >= this.
    Pseudo rows (run/notify/config) and sources without enough history are never flagged.
    """
    latest = {r["source_key"]: r for r in health(conn)}

    # Successful per-source counts, newest first, for the baseline comparison.
    ok_counts: dict[str, list[int]] = {}
    for r in conn.execute("SELECT source_key, count FROM runs WHERE ok = 1 ORDER BY id DESC"):
        ok_counts.setdefault(r["source_key"], []).append(r["count"])

    out: list[dict] = []
    for key in sorted(latest):
        row = latest[key]
        quiet, reason = _quiet_signal(
            key, ok_counts.get(key, []),
            window=window, drop_ratio=drop_ratio, baseline_floor=baseline_floor,
        )
        out.append({
            "source_key": key, "started_at": row["started_at"], "ok": row["ok"],
            "count": row["count"], "dropped": row["dropped"], "error": row["error"],
            "quiet": quiet, "quiet_reason": reason,
        })
    return out


def _quiet_signal(
    source_key: str,
    ok_counts: list[int],
    *,
    window: int,
    drop_ratio: float,
    baseline_floor: int,
) -> tuple[bool, str]:
    """(quiet, reason) for one source from its successful run counts (newest first)."""
    if ":" not in source_key:  # pseudo row (run/notify/config) — not a fetched board
        return False, ""
    if len(ok_counts) < 2:  # need a latest + at least one prior to compare
        return False, ""
    latest = ok_counts[0]
    prior = ok_counts[1:1 + window]
    baseline = sum(prior) / len(prior)
    if baseline < baseline_floor:  # too small to tell breakage from normal churn
        return False, ""
    if latest == 0:
        return True, f"went quiet — 0 vs recent ~{baseline:.0f}/run"
    if latest < drop_ratio * baseline:
        return True, f"sharp drop — {latest} vs recent ~{baseline:.0f}/run"
    return False, ""


def pending_counts(conn: sqlite3.Connection) -> tuple[int, int]:
    """(total pending, keyword-candidate pending) for the review queue + nudge."""
    total = conn.execute(
        "SELECT COUNT(*) FROM postings WHERE review_status = 'pending'"
    ).fetchone()[0]
    candidates = conn.execute(
        "SELECT COUNT(*) FROM postings WHERE review_status = 'pending' "
        "AND (is_cs_relevant = 1 OR is_internship = 1 OR is_newgrad = 1)"
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
    """Log one run row. `dropped` = titles the source's flood guard rejected before storing
    (0 for the heartbeat/notify/config pseudo-rows that don't fetch)."""
    conn.execute(
        "INSERT INTO runs (started_at, source_key, ok, count, error, dropped) "
        "VALUES (?,?,?,?,?,?)",
        (now, source_key, int(ok), count, error, dropped),
    )
    conn.commit()


def set_application(
    conn: sqlite3.Connection,
    posting_id: str,
    status: str | None = None,
    notes: str | None = None,
    applied_date: str | None = None,
) -> None:
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
