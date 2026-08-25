import pytest

from internshelper import companygroups


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
