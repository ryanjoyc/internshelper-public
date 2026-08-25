"""Company-authoritative grouping and persisted posting group values."""

import pytest

from internshelper import companygroups, db, store, tiers
from internshelper.models import Posting
from internshelper.review import set_verdict

NOW = "2026-07-11T10:00:00+00:00"


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("Google", "google"), ("Google LLC", "google"),
        ("Uber Technologies, Inc.", "uber technologies"),
        ("D. E. Shaw & Co.", "de shaw"), ("DE Shaw", "de shaw"),
        ("Citadel Securities", "citadel securities"), ("  NVIDIA  ", "nvidia"),
        ("A. B. C. Corp", "abc"), ("Company", "company"), ("", ""),
    ],
)
def test_normalize_company(raw, normalized):
    assert tiers.normalize_company(raw) == normalized


def _row(company, score=None):
    return {"company": company, "rank_score": score}


def test_compute_tier_uses_company_only_and_never_score():
    entries = [
        companygroups.CompanyGroupEntry("Google", "top_target", aliases=["Alphabet"]),
        companygroups.CompanyGroupEntry("Acme", "discovery", reason="Interesting infra"),
    ]
    mapping = tiers.company_tier_map(entries)
    assert tiers.compute_tier(_row("Google LLC", 0.01), mapping) == "top_target"
    assert tiers.compute_tier(_row("Alphabet", None), mapping) == "top_target"
    assert tiers.compute_tier(_row("Acme", 0.99), mapping) == "discovery"
    assert tiers.compute_tier(_row("Unknown", 0.99), mapping) == "unclassified"


def _tiered_db(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    for pid, company in [("g:1", "Google"), ("g:2", "Acme"), ("g:3", "Acme"),
                         ("g:4", "Acme")]:
        store.upsert(conn, Posting(
            posting_id=pid, source_key="greenhouse:x", title="SWE Intern",
            company=company, url="https://x", is_internship=True), now=NOW)
    conn.execute("UPDATE postings SET rank_score = 0.1 WHERE posting_id = 'g:1'")
    conn.execute("UPDATE postings SET rank_score = 0.99 WHERE posting_id = 'g:2'")
    set_verdict(conn, "g:4", "no_match", "dismissed: board", now=NOW)
    conn.commit()
    return conn


def test_retier_inbox_moves_whole_company_and_skips_dismissed(tmp_path):
    conn = _tiered_db(tmp_path)
    mapping = tiers.company_tier_map([
        companygroups.CompanyGroupEntry("Google", "top_target"),
        companygroups.CompanyGroupEntry("Acme", "known"),
    ])
    assert tiers.retier_inbox(conn, mapping) == 3
    got = {r["posting_id"]: r["tier"] for r in conn.execute(
        "SELECT posting_id, tier FROM postings")}
    assert got == {"g:1": "top_target", "g:2": "known", "g:3": "known", "g:4": None}


def test_load_tier_map_reads_group_yaml_and_aliases(tmp_path):
    path = tmp_path / "company-groups.yaml"
    path.write_text(
        "companies:\n"
        "  - name: Meta\n    group: top_target\n    aliases: [Facebook]\n"
        "  - name: TinyCo\n    group: discovery\n    reason: Useful systems work\n"
        "  - group: known\n"
    )
    mapping = tiers.load_tier_map(path)
    assert mapping["meta"].group == "top_target"
    assert mapping["facebook"].name == "Meta"
    assert mapping["tinyco"].group == "discovery"
    assert "google" not in mapping  # no hidden built-in classifications
