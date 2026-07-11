"""Apply-order tiers: which postings to apply to FIRST.

There is no approval gate — every collected posting is in the Inbox. The tiers order
the work: the company decides the tier, the learned rank_score orders within it (and
demotes likely-junk to "Long shots" regardless of company).

    apply_first      "Apply first"      dream companies (built-in list + companies.yaml
                                        entries marked `tier: dream`)
    target           "Then these"       every other company in the approved index
    everything_else  "Everything else"  unlisted companies
    long_shots       "Long shots"       rank_score < LONG_SHOT_THRESHOLD (any company),
                                        or unscored cold-start non-candidates

`tier` on a posting row is always the COMPUTED tier; a user drag pins `pinned_tier`,
and the effective tier is COALESCE(pinned_tier, tier) — `retier_inbox` rewrites only
`tier`, so a pinned card never moves.

Company matching is exact on `normalize_company` output — deliberately no substring
matching ("Citadel" and "Citadel Securities" are two real firms; both are listed
explicitly). If real misses appear (e.g. "Google DeepMind"), add aliases then.
"""

from __future__ import annotations

import sqlite3

from internshelper import companies as companies_mod
from internshelper import store

TIERS = ("apply_first", "target", "everything_else", "long_shots")

TIER_LABELS = {
    "apply_first": "Apply first",
    "target": "Then these",
    "everything_else": "Everything else",
    "long_shots": "Long shots",
}

# Same value as the old review "probably not" cutoff (ranking's sigmoid output).
LONG_SHOT_THRESHOLD = 0.35

# Corporate-suffix tokens dropped from the END of a name ("Uber Technologies, Inc."
# still won't match "Uber" — the list below names companies the way boards do).
_SUFFIX_TOKENS = frozenset(
    {"inc", "llc", "ltd", "corp", "corporation", "co", "company", "plc", "gmbh"}
)

_PUNCT = str.maketrans({c: " " for c in ".,'\"&()/"})

_DREAM_RAW = (
    "Google", "Meta", "Apple", "Amazon", "Microsoft", "Netflix", "NVIDIA",
    "OpenAI", "Anthropic", "Stripe", "Jane Street", "Citadel", "Citadel Securities",
    "Two Sigma", "D. E. Shaw", "Hudson River Trading", "Jump Trading",
    "Databricks", "Palantir", "SpaceX", "Airbnb", "Uber", "Figma", "Ramp",
)


def normalize_company(name: str) -> str:
    """Canonical form for company matching: lowercase, punctuation stripped, trailing
    legal suffixes dropped, consecutive single-letter initials merged (so
    "D. E. Shaw & Co." and "DE Shaw" both become "de shaw")."""
    tokens = (name or "").lower().translate(_PUNCT).split()
    while len(tokens) > 1 and tokens[-1] in _SUFFIX_TOKENS:
        tokens.pop()
    merged: list[str] = []
    run: list[str] = []  # consecutive single-letter tokens fuse: "d e shaw" -> "de shaw"
    for t in tokens:
        if len(t) == 1:
            run.append(t)
        else:
            if run:
                merged.append("".join(run))
                run = []
            merged.append(t)
    if run:
        merged.append("".join(run))
    return " ".join(merged)


DEFAULT_DREAM_COMPANIES = frozenset(normalize_company(n) for n in _DREAM_RAW)


def company_tier_map(entries) -> dict[str, str]:
    """normalized company name -> "dream" | "target".

    Built-ins are dream; approved-index entries are target unless marked
    `tier: dream`. A built-in dream is never demoted by a plain index entry.
    """
    tier_map = {n: "dream" for n in DEFAULT_DREAM_COMPANIES}
    for e in entries:
        key = normalize_company(e.name)
        if not key:
            continue
        if getattr(e, "tier", "") == "dream":
            tier_map[key] = "dream"
        else:
            tier_map.setdefault(key, "target")
    return tier_map


def load_tier_map(path=None) -> dict[str, str]:
    """The tier map from the approved-companies index (malformed entries skipped —
    tiering must not break when one yaml entry does)."""
    entries, _errors = companies_mod.load_companies(path or companies_mod.companies_path())
    return company_tier_map(entries)


def compute_tier(row, tier_map: dict[str, str]) -> str:
    """The computed tier for one posting row (pins are the caller's concern)."""
    score = row["rank_score"]
    if score is not None and score < LONG_SHOT_THRESHOLD:
        return "long_shots"
    if score is None and not store.is_candidate(row):
        return "long_shots"  # cold start: the keyword heuristic is all we have
    company_tier = tier_map.get(normalize_company(row["company"] or ""))
    if company_tier == "dream":
        return "apply_first"
    if company_tier == "target":
        return "target"
    return "everything_else"


def retier_inbox(conn: sqlite3.Connection, tier_map: dict[str, str]) -> int:
    """Recompute `tier` for every non-dismissed posting (one transaction, idempotent).

    Pinned rows get their computed tier refreshed too — `pinned_tier` wins at read
    time, so this never moves a pinned card. Returns the number of rows updated.
    """
    rows = conn.execute(
        "SELECT posting_id, company, rank_score, is_internship, is_newgrad, is_cs_relevant "
        "FROM postings WHERE verdict IS NULL OR verdict != 'no_match'"
    ).fetchall()
    updates = [(compute_tier(r, tier_map), r["posting_id"]) for r in rows]
    conn.executemany("UPDATE postings SET tier = ? WHERE posting_id = ?", updates)
    conn.commit()
    return len(updates)
