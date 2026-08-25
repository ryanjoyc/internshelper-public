"""Companies page: browsing groups plus board-resolution Approval A."""

from internshelper import companies, companygroups, config


def test_companies_page_renders_groups(client, companies_file):
    companies.add_company(companies_file, "Goldman Sachs")
    companies.add_company(companies_file, "TinyStartup")
    companies.mark_no_board(companies_file, "TinyStartup", notes="nothing public")
    r = client.get("/companies")
    assert r.status_code == 200
    assert "Goldman Sachs" in r.text and "TinyStartup" in r.text
    assert "no-board" in r.text


def test_companies_page_flags_dangling_pointer(client, companies_file):
    companies.add_company(companies_file, "Ghost")
    # link to a source that doesn't exist -> must render as a warning, not a 500
    entries, _ = companies.load_companies(companies_file)
    entries[0].status, entries[0].board = "resolved", "greenhouse:gone"
    companies.save_companies(companies_file, entries)
    r = client.get("/companies")
    assert r.status_code == 200
    assert "Ghost" in r.text and "gone" in r.text


def test_add_company_via_form(client, companies_file):
    r = client.post("/companies/add", data={"name": "Goldman Sachs"})
    assert r.status_code == 200
    assert "Goldman Sachs" in r.text
    assert companies.load_companies(companies_file)[0][0].status == "pending"


def test_add_duplicate_flashes_not_500(client, companies_file):
    companies.add_company(companies_file, "Goldman Sachs")
    r = client.post("/companies/add", data={"name": "Goldman Sachs"})
    assert r.status_code == 200
    assert "already" in r.text


def test_approve_proposal_writes_source_and_resolves(client, companies_file,
                                                     sources_file, monkeypatch):
    companies.add_company(companies_file, "Stripe")
    companies.set_proposal(companies_file, "Stripe",
                           url="https://boards.greenhouse.io/stripe", count=9,
                           evidence="embedded board")
    r = client.post("/companies/approve", data={"name": "Stripe"})
    assert r.status_code == 200
    assert [e.source_key for e in config.load_sources(sources_file)[0]] == ["greenhouse:stripe"]
    assert companies.load_companies(companies_file)[0][0].status == "resolved"


def test_reject_proposal_marks_no_board(client, companies_file):
    companies.add_company(companies_file, "TinyStartup")
    companies.set_proposal(companies_file, "TinyStartup", url="https://x", count=0,
                           evidence="ev")
    r = client.post("/companies/reject", data={"name": "TinyStartup",
                                               "notes": "wrong board"})
    assert r.status_code == 200
    e = companies.load_companies(companies_file)[0][0]
    assert e.status == "no-board" and e.proposal == {}


def test_set_company_group_round_trip(client, company_groups_file):
    r = client.post("/companies/group/set", data={"name": "Capital One", "group": "known"})
    assert r.status_code == 200
    assert companygroups.find(companygroups.load_company_groups(company_groups_file)[0],
                              "Capital One").group == "known"
    assert "Capital One" in r.text and "Known companies" in r.text


def test_discovery_proposal_approve_and_reject(client, company_groups_file):
    reason = "Developer platform"
    evidence = "Public products and a supported careers board"
    companygroups.propose_discovery(
        company_groups_file, "Cloudflare", reason=reason, evidence=evidence,
    )
    r = client.get("/companies")
    assert "Cloudflare" in r.text and "Worth discovering proposals" in r.text
    assert "View evidence" in r.text and "1 pending" in r.text
    assert "0 active postings" in r.text

    r = client.post("/companies/group/approve", data={"name": "Cloudflare"})
    assert "approved" in r.text and "/companies/group/undo-approve" in r.text
    entry = companygroups.find(companygroups.load_company_groups(company_groups_file)[0],
                               "Cloudflare")
    assert entry.group == "discovery" and entry.reason == "Developer platform"

    r = client.post("/companies/group/undo-approve", data={
        "name": "Cloudflare", "reason": reason, "evidence": evidence,
    })
    assert "restored discovery proposal" in r.text and "Cloudflare" in r.text
    entry = companygroups.find(companygroups.load_company_groups(company_groups_file)[0],
                               "Cloudflare")
    assert entry.group == "" and entry.proposal["reason"] == reason

    companygroups.propose_discovery(
        company_groups_file, "Acme", reason="Interesting", evidence="Evidence")
    r = client.post("/companies/group/reject", data={"name": "Acme"})
    assert "rejected" in r.text and "/companies/group/undo-reject" in r.text
    assert companygroups.find(companygroups.load_company_groups(company_groups_file)[0],
                              "Acme") is None

    r = client.post("/companies/group/undo-reject", data={
        "name": "Acme", "reason": "Interesting", "evidence": "Evidence",
    })
    assert "restored discovery proposal" in r.text and "Acme" in r.text
    entry = companygroups.find(companygroups.load_company_groups(company_groups_file)[0],
                               "Acme")
    assert entry.group == "" and entry.proposal["evidence"] == "Evidence"


def test_undo_discovery_approval_restores_previous_group(
    client, company_groups_file
):
    companygroups.set_group(company_groups_file, "Cloudflare", "known")
    companygroups.propose_discovery(
        company_groups_file,
        "Cloudflare",
        reason="Developer platform",
        evidence="Official product and careers evidence",
    )
    client.post("/companies/group/approve", data={"name": "Cloudflare"})

    r = client.post("/companies/group/undo-approve", data={
        "name": "Cloudflare",
        "reason": "Developer platform",
        "evidence": "Official product and careers evidence",
        "previous_group": "known",
        "previous_reason": "",
    })
    assert r.status_code == 200
    entry = companygroups.find(
        companygroups.load_company_groups(company_groups_file)[0], "Cloudflare"
    )
    assert entry.group == "known"
    assert entry.proposal["group"] == "discovery"


def test_companies_page_cannot_bypass_discovery_approval(client, company_groups_file):
    r = client.post(
        "/companies/group/set", data={"name": "Acme", "group": "discovery"}
    )
    assert "proposal and approval" in r.text
    assert companygroups.find(companygroups.load_company_groups(company_groups_file)[0],
                              "Acme") is None
