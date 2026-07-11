"""Companies page: index (Approval A) — add, approve, reject."""

from internshelper import companies, config


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
