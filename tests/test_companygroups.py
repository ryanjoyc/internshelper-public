import pytest

from internshelper import companygroups, db, store
from internshelper.models import Posting


def test_set_clear_and_round_trip(tmp_path):
    path = tmp_path / "groups.yaml"
    companygroups.set_group(path, "Capital One", "known")
    entries, errors = companygroups.load_company_groups(path)
    assert not errors and entries[0].group == "known"
    companygroups.clear_group(path, "Capital One")
    assert companygroups.load_company_groups(path)[0] == []


def test_discovery_proposal_requires_explicit_approval(tmp_path):
    path = tmp_path / "groups.yaml"
    companygroups.propose_discovery(
        path, "Cloudflare", reason="Strong developer platform",
        evidence="Public engineering work and broad internship board",
    )
    entry = companygroups.load_company_groups(path)[0][0]
    assert entry.group == "" and entry.proposal["group"] == "discovery"
    companygroups.approve_proposal(path, "Cloudflare")
    entry = companygroups.load_company_groups(path)[0][0]
    assert entry.group == "discovery" and entry.proposal == {}
    assert entry.reason == "Strong developer platform"


def test_reject_discovery_proposal_removes_unclassified_stub(tmp_path):
    path = tmp_path / "groups.yaml"
    companygroups.propose_discovery(path, "Acme", reason="Why", evidence="Evidence")
    companygroups.reject_proposal(path, "Acme")
    assert companygroups.load_company_groups(path)[0] == []


def test_discovery_cannot_bypass_proposal_approval(tmp_path):
    with pytest.raises(ValueError, match="proposal and approval"):
        companygroups.set_group(tmp_path / "groups.yaml", "Acme", "discovery")


def test_cli_group_change_syncs_persisted_posting_tiers(tmp_path, monkeypatch):
    groups_path = tmp_path / "groups.yaml"
    db_path = tmp_path / "internshelper.db"
    companygroups.set_group(groups_path, "Acme", "top_target")
    conn = db.connect(db_path)
    db.init_db(conn)
    store.upsert(
        conn,
        Posting(
            posting_id="greenhouse:acme",
            source_key="greenhouse:acme",
            title="Software Engineering Intern",
            company="Acme",
            url="https://example.test/acme",
        ),
        now="2026-08-27T12:00:00+00:00",
    )
    conn.execute(
        "UPDATE postings SET tier = 'top_target' WHERE posting_id = 'greenhouse:acme'"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("INTERNSHELPER_COMPANY_GROUPS", str(groups_path))
    monkeypatch.setenv("INTERNSHELPER_DB", str(db_path))

    assert companygroups.main(["set", "Acme", "known"]) == 0
    conn = db.connect(db_path)
    assert conn.execute(
        "SELECT tier FROM postings WHERE posting_id = 'greenhouse:acme'"
    ).fetchone()[0] == "known"
    conn.close()
