"""Company-authoritative browsing groups for Inbox postings.

Every broadly relevant posting stays visible. A company's explicit group decides
where all of its postings appear; rank scores only order roles within the company
and never classify or demote them. Missing companies are neutrally unclassified.

The historical ``tier`` database column is retained as a compatibility/storage
field, but its values now mirror the four company browsing groups.
"""

from __future__ import annotations

import sqlite3

from internshelper import companygroups

TIERS = companygroups.ALL_GROUPS
TIER_LABELS = companygroups.GROUP_LABELS
TIER_HINTS = companygroups.GROUP_HINTS
normalize_company = companygroups.normalize_company


def company_tier_map(entries) -> dict[str, companygroups.CompanyGroupEntry]:
    """Return canonical and alias identities mapped to their configured entry."""
    return companygroups.group_map(entries)


def load_tier_map(path=None) -> dict[str, companygroups.CompanyGroupEntry]:
    """Load the independent company browsing-group index."""
    entries, _errors = companygroups.load_company_groups(
        path or companygroups.company_groups_path()
    )
    return company_tier_map(entries)


def compute_tier(row, tier_map) -> str:
    """Return the company's explicit group, or the neutral default.

    Deliberately does not inspect rank_score or role flags: collection guards decide
    broad relevance, while company organization remains a user-controlled lens.
    """
    group, _entry = companygroups.classify(row["company"] or "", tier_map)
    return group


def retier_inbox(conn: sqlite3.Connection, tier_map) -> int:
    """Persist company groups for every non-dismissed posting, idempotently."""
    rows = conn.execute(
        "SELECT posting_id, company FROM postings "
        "WHERE verdict IS NULL OR verdict != 'no_match'"
    ).fetchall()
    updates = [(compute_tier(row, tier_map), row["posting_id"]) for row in rows]
    conn.executemany("UPDATE postings SET tier = ? WHERE posting_id = ?", updates)
    conn.commit()
    return len(updates)
