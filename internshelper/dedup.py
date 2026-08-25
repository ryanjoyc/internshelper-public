"""Cross-source de-duplication: the same real-world job listed on more than one board.

Postings are keyed by `posting_id = "{type}:{native-id}"`, so one job appearing on two
sources (e.g. a careers URL linked by several GitHub lists under different company labels)
gets distinct primary keys and stores twice. This module reconciles that AFTER the per-source
upserts, in two tiers:

- **Strong URL match — auto.** Two postings whose apply URLs reduce to the same
  `host + ATS job-id` are the same job with near-certainty; they collapse silently
  (`collapse_url_duplicates`). This catches the SIG/Susquehanna case: `careers.sig.com/jobs/10838`,
  `careers.sig.com/intern-co-op/jobs/10838`, and `.../jobs/10838?utm_source=…` all key to
  `careers.sig.com#10838` regardless of path prefix, query, company label, or title.
- **Company + title lookalike — human-confirmed.** Rows sharing a normalized company + title are
  only *suggested* (`find_possible_duplicates`) for the Board's Duplicates review lens, because a
  shared title can be genuinely distinct roles (e.g. the same title in five cities).

Resolution is soft + reversible: the loser gets `duplicate_of = <survivor>` (hidden from the
Inbox + digest via `store.INBOX_SQL`, never deleted). `dedup_keep=1` records a "keep separate"
decision so a rejected suggestion stops resurfacing.
"""

from __future__ import annotations

import re
import sqlite3
from collections import OrderedDict
from urllib.parse import parse_qsl, urlsplit

from internshelper import store
from internshelper.tiers import normalize_company

# The auto (silent) URL key stays conservative: only a >=5-digit numeric path segment counts as
# an ATS job id. That captures SIG (5-digit) and Greenhouse (7-digit) ids while excluding 4-digit
# years like "2027" that appear in paths. Anything shorter/ambiguous falls through to the
# human-confirmed company+title queue rather than risk a silent false merge.
_MIN_ID_DIGITS = 5
_NUMERIC_SEG = re.compile(r"^\d+$")
_TITLE_KEEP = re.compile(r"[^a-z0-9]+")

# Query params that carry the job's identity (some ATSes host a generic path like
# `/jobs/search` and put the id in the query). Checked before the canonical-path fallback so
# distinct jobs on a shared path don't wrongly merge.
_ID_QUERY_KEYS = ("gh_jid", "jobid", "job_id", "reqid", "req_id", "posting_id")
# Tracking/junk params dropped from the canonical-path fallback so the same link with different
# wrappers still matches. Anything NOT listed here is kept as identity (safe: a kept param at
# worst prevents a merge, never forces a wrong one).
_TRACKING_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "gh_src", "src", "source", "ref", "referrer", "lang", "mode", "iis", "jr_id", "trackingid",
}


def url_dedup_key(url: str | None) -> str | None:
    """A high-precision identity key for an apply URL, or None if the URL is unusable.

    Resolution order (each shape is safe to auto-collapse on):
    1. `host#jobid` from a stable ATS job id in the PATH (last purely-numeric segment with at least
       `_MIN_ID_DIGITS` digits). Path prefix, query and fragment are ignored — this unifies the SIG
       variants (`/jobs/10838` == `/intern-co-op/jobs/10838?utm=…`).
    2. `host#jobid` from an id-bearing QUERY param (`gh_jid`, …) when the path has no numeric id.
       Greenhouse hosts a generic `/jobs/search?gh_jid=7718947`, so the id MUST come from the query
       or every job on that path would wrongly merge.
    3. Otherwise the canonical `host/path` plus any non-tracking query params (utm_* etc. dropped,
       the rest kept + sorted). Only genuinely identical links collapse here — this also catches
       non-numeric ids (e.g. Lever UUIDs) linked with different tracking wrappers by two lists.

    The `#` and `/` key spaces can't collide.
    """
    if not url:
        return None
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None
    path = parts.path.rstrip("/")
    query = parse_qsl(parts.query, keep_blank_values=False)

    path_ids = [
        seg for seg in path.split("/")
        if _NUMERIC_SEG.match(seg) and len(seg) >= _MIN_ID_DIGITS
    ]
    if path_ids:
        return f"{host}#{path_ids[-1]}"

    for k, v in query:
        if k.lower() in _ID_QUERY_KEYS and _NUMERIC_SEG.match(v) and len(v) >= _MIN_ID_DIGITS:
            return f"{host}#{v}"

    kept = sorted((k, v) for k, v in query if k.lower() not in _TRACKING_KEYS)
    if kept:
        qs = "&".join(f"{k}={v}" for k, v in kept)
        return f"{host}{path}?{qs}"
    return f"{host}{path}" if path else None


def title_norm(title: str | None) -> str:
    """Lowercased, punctuation/emoji-stripped, whitespace-collapsed title for fuzzy grouping."""
    return _TITLE_KEEP.sub(" ", (title or "").lower()).strip()


def _pick_survivor(rows: list[sqlite3.Row]) -> sqlite3.Row:
    """The row to KEEP from a duplicate group (the rest point at it).

    Prefer active > higher rank_score > richer description > earliest first_seen, with
    posting_id as a stable final tiebreak so the choice is deterministic across runs.
    """
    def key(r: sqlite3.Row):
        return (
            0 if r["is_active"] else 1,
            -(r["rank_score"] if r["rank_score"] is not None else -1.0),
            -len(r["description"] or ""),
            r["first_seen"] or "",
            r["posting_id"],
        )
    return min(rows, key=key)


def _group(rows, key_fn) -> list[list[sqlite3.Row]]:
    """Bucket rows by key_fn (skipping None keys); return only groups with >1 member."""
    buckets: "OrderedDict[str, list]" = OrderedDict()
    for r in rows:
        k = key_fn(r)
        if k is None:
            continue
        buckets.setdefault(k, []).append(r)
    return [g for g in buckets.values() if len(g) > 1]


def collapse_url_duplicates(conn: sqlite3.Connection) -> int:
    """Auto-collapse strong URL-match duplicates; returns the number of rows hidden.

    Only unresolved rows (`duplicate_of IS NULL`) are candidates, so survivors from a prior run
    stay survivors and a newly-arrived same-key row simply joins the existing group. Idempotent.
    """
    rows = conn.execute(
        "SELECT posting_id, url, is_active, rank_score, description, first_seen "
        "FROM postings WHERE duplicate_of IS NULL"
    ).fetchall()
    updates: list[tuple[str, str]] = []
    for group in _group(rows, lambda r: url_dedup_key(r["url"])):
        survivor = _pick_survivor(group)
        for r in group:
            if r["posting_id"] != survivor["posting_id"]:
                updates.append((survivor["posting_id"], r["posting_id"]))
    if updates:
        conn.executemany(
            "UPDATE postings SET duplicate_of = ? WHERE posting_id = ?", updates
        )
        conn.commit()
    return len(updates)


def find_possible_duplicates(conn: sqlite3.Connection) -> list[dict]:
    """Company+title lookalike groups awaiting human confirmation (live-computed, not stored).

    Candidates are inbox-scope, active, not already a duplicate, and not marked keep-separate.
    Each returned group carries every row's company/title/location/url/source so the reviewer can
    see the differences before merging. Survivor suggestion = `_pick_survivor`.
    """
    rows = conn.execute(
        "SELECT posting_id, company, title, location, url, source_key, "
        "is_active, rank_score, description, first_seen "
        f"FROM postings WHERE {store.INBOX_SQL} AND is_active = 1 AND dedup_keep = 0"
    ).fetchall()
    out: list[dict] = []
    for group in _group(rows, lambda r: f"{normalize_company(r['company'] or '')}|{title_norm(r['title'])}"):
        survivor = _pick_survivor(group)
        out.append({
            "company": survivor["company"] or "Unknown",
            "title": survivor["title"],
            "survivor_id": survivor["posting_id"],
            "rows": [{
                "posting_id": r["posting_id"], "company": r["company"], "title": r["title"],
                "location": r["location"], "url": r["url"], "source_key": r["source_key"],
                "is_survivor": r["posting_id"] == survivor["posting_id"],
            } for r in group],
        })
    return out
