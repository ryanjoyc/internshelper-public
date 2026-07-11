"""Apply-order tiers: company normalization, tier rules, and retier_inbox."""

import pytest

from internshelper import db, store, tiers
from internshelper.companies import CompanyEntry
from internshelper.models import Posting
from internshelper.review import set_verdict

NOW = "2026-07-11T10:00:00+00:00"


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("Google", "google"),
        ("Google LLC", "google"),
        ("Uber Technologies, Inc.", "uber technologies"),
        ("D. E. Shaw & Co.", "de shaw"),
        ("DE Shaw", "de shaw"),
        ("Citadel Securities", "citadel securities"),
        ("  NVIDIA  ", "nvidia"),
        ("A. B. C. Corp", "abc"),
        ("Company", "company"),  # a lone suffix token is a name, not a suffix
        ("", ""),
    ],
)
def test_normalize_company(raw, normalized):
    assert tiers.normalize_company(raw) == normalized


def _row(company, score, cand=True):
    return {
        "company": company, "rank_score": score,
        "is_cs_relevant": cand, "is_internship": False, "is_newgrad": False,
    }


def test_compute_tier_company_decides_score_can_demote():
    tmap = tiers.company_tier_map([CompanyEntry(name="Ramp Startup")])
    assert tiers.compute_tier(_row("Google LLC", 0.9), tmap) == "apply_first"
    assert tiers.compute_tier(_row("Ramp Startup", 0.9), tmap) == "target"
    assert tiers.compute_tier(_row("Acme Corp", 0.9), tmap) == "everything_else"
    # A low score buries the posting no matter how dreamy the company.
    assert tiers.compute_tier(_row("Google", 0.1), tmap) == "long_shots"


def test_compute_tier_cold_start_uses_candidate_heuristic():
    tmap = tiers.company_tier_map([])
    assert tiers.compute_tier(_row("Google", None, cand=True), tmap) == "apply_first"
    assert tiers.compute_tier(_row("Acme", None, cand=True), tmap) == "everything_else"
    assert tiers.compute_tier(_row("Google", None, cand=False), tmap) == "long_shots"


def test_company_tier_map_merges_builtins_and_index():
    entries = [
        CompanyEntry(name="Google"),                      # plain entry never demotes a builtin
        CompanyEntry(name="Jane Street Capital", tier="dream"),
        CompanyEntry(name="Stripe Inc"),                  # normalizes onto builtin "stripe"
        CompanyEntry(name="Ramp Startup"),
    ]
    tmap = tiers.company_tier_map(entries)
    assert tmap["google"] == "dream"
    assert tmap["jane street capital"] == "dream"
    assert tmap["stripe"] == "dream"
    assert tmap["ramp startup"] == "target"
    assert "acme" not in tmap


def _tiered_db(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    for pid, company in [("g:1", "Google"), ("g:2", "Acme"), ("g:3", "Acme"), ("g:4", "Acme")]:
        store.upsert(conn, Posting(
            posting_id=pid, source_key="greenhouse:x", title="SWE Intern",
            company=company, url="https://x", is_internship=True), now=NOW)
    conn.execute("UPDATE postings SET rank_score = 0.8")
    conn.execute("UPDATE postings SET rank_score = 0.1 WHERE posting_id = 'g:3'")
    set_verdict(conn, "g:4", "no_match", "dismissed: board", now=NOW)
    conn.commit()
    return conn


def test_retier_inbox_updates_everything_but_dismissed(tmp_path):
    conn = _tiered_db(tmp_path)
    n = tiers.retier_inbox(conn, tiers.company_tier_map([]))
    assert n == 3
    got = {r["posting_id"]: r["tier"] for r in conn.execute("SELECT posting_id, tier FROM postings")}
    assert got == {"g:1": "apply_first", "g:2": "everything_else",
                   "g:3": "long_shots", "g:4": None}


def test_retier_inbox_is_idempotent_and_never_moves_a_pin(tmp_path):
    conn = _tiered_db(tmp_path)
    tmap = tiers.company_tier_map([])
    tiers.retier_inbox(conn, tmap)
    conn.execute("UPDATE postings SET pinned_tier = 'apply_first' WHERE posting_id = 'g:3'")
    conn.commit()
    tiers.retier_inbox(conn, tmap)  # second pass: same computed tiers, pin untouched
    row = conn.execute(
        "SELECT tier, pinned_tier FROM postings WHERE posting_id = 'g:3'").fetchone()
    assert row["tier"] == "long_shots"          # computed tier keeps updating...
    assert row["pinned_tier"] == "apply_first"  # ...but the pin (the effective tier) stays


def test_load_tier_map_reads_companies_yaml(tmp_path):
    f = tmp_path / "companies.yaml"
    f.write_text(
        "companies:\n"
        "  - name: Jane Street Capital\n"
        "    tier: dream\n"
        "  - name: Ramp Startup\n"
        "  - status: pending\n"   # malformed (nameless) — skipped, never fatal
    )
    tmap = tiers.load_tier_map(f)
    assert tmap["jane street capital"] == "dream"
    assert tmap["ramp startup"] == "target"
    assert tmap["google"] == "dream"  # builtins always present
