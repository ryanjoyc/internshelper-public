"""The approved-companies index: load/validate/save + mutations + CLI."""
import json

import pytest

from internshelper import companies
from internshelper.companies import CompanyEntry


def _write(p, text):
    p.write_text(text)
    return p


def test_load_missing_file_is_empty(tmp_path):
    assert companies.load_companies(tmp_path / "nope.yaml") == ([], [])


def test_load_parses_states_and_collects_errors(tmp_path):
    f = _write(tmp_path / "c.yaml", """
companies:
  - name: Visa
    status: resolved
    board: workday:https://visa.wd5.myworkdayjobs.com/Visa
  - name: Goldman Sachs
  - name: TinyStartup
    status: no-board
    notes: nothing public
  - name: Broken
    status: resolved
  - status: pending
""")
    entries, errors = companies.load_companies(f)
    assert [e.name for e in entries] == ["Visa", "Goldman Sachs", "TinyStartup"]
    assert entries[0].status == "resolved" and entries[0].board.startswith("workday:")
    assert entries[1].status == "pending"          # default
    assert entries[2].notes == "nothing public"
    assert len(errors) == 2                        # resolved-without-board, nameless
    assert "board" in errors[0]["reason"]


def test_load_rejects_duplicate_names_case_insensitive(tmp_path):
    f = _write(tmp_path / "c.yaml", "companies:\n  - name: Visa\n  - name: VISA\n")
    entries, errors = companies.load_companies(f)
    assert [e.name for e in entries] == ["Visa"]
    assert "duplicate" in errors[0]["reason"]


def test_save_round_trips_and_is_machine_managed(tmp_path):
    f = tmp_path / "c.yaml"
    companies.save_companies(f, [
        CompanyEntry(name="Visa", status="resolved", board="workday:x"),
        CompanyEntry(name="Goldman Sachs", proposal={"url": "https://g", "count": 3,
                                                     "evidence": "found it"}),
    ])
    entries, errors = companies.load_companies(f)
    assert errors == []
    assert entries[0].board == "workday:x"
    assert entries[1].proposal["count"] == 3
    assert f.read_text().startswith("#")           # fixed header present


def test_find_is_case_insensitive():
    es = [CompanyEntry(name="Goldman Sachs")]
    assert companies.find(es, "goldman sachs") is es[0]
    assert companies.find(es, "Nope") is None


# ---------- mutations ----------

@pytest.fixture
def cfile(tmp_path, monkeypatch):
    p = tmp_path / "companies.yaml"
    monkeypatch.setenv("INTERNSHELPER_COMPANIES", str(p))
    return p


@pytest.fixture
def sfile(tmp_path, monkeypatch):
    p = tmp_path / "sources.yaml"
    p.write_text("sources:\n")
    monkeypatch.setenv("INTERNSHELPER_SOURCES", str(p))
    return p


def test_add_company_appends_pending_and_rejects_duplicates(cfile):
    e = companies.add_company(cfile, "Goldman Sachs")
    assert e.status == "pending"
    with pytest.raises(ValueError, match="already"):
        companies.add_company(cfile, "goldman sachs")
    assert [x.name for x in companies.load_companies(cfile)[0]] == ["Goldman Sachs"]


def test_set_proposal_only_on_pending(cfile):
    companies.add_company(cfile, "Goldman Sachs")
    companies.set_proposal(cfile, "Goldman Sachs",
                           url="https://gs.wd5.myworkdayjobs.com/GS", count=42,
                           evidence="careers page links this tenant")
    e = companies.load_companies(cfile)[0][0]
    assert e.proposal == {"url": "https://gs.wd5.myworkdayjobs.com/GS", "count": 42,
                          "evidence": "careers page links this tenant"}
    companies.mark_no_board(cfile, "Goldman Sachs")
    with pytest.raises(ValueError, match="pending"):
        companies.set_proposal(cfile, "Goldman Sachs", url="x", count=0, evidence="")


def test_approve_writes_source_with_guard_and_links(cfile, sfile):
    companies.add_company(cfile, "Goldman Sachs")
    companies.set_proposal(cfile, "Goldman Sachs",
                           url="https://gs.wd5.myworkdayjobs.com/GS", count=42, evidence="ev")
    entry = companies.approve(cfile, sfile, "Goldman Sachs")
    assert entry.type == "workday" and entry.search == "intern"
    assert "internship" in entry.title_must_match
    from internshelper import config
    written = config.load_sources(sfile)[0]
    assert [s.source_key for s in written] == [entry.source_key]
    c = companies.load_companies(cfile)[0][0]
    assert c.status == "resolved" and c.board == entry.source_key and c.proposal == {}


def test_approve_dedupes_existing_source(cfile, sfile):
    sfile.write_text("sources:\n  - type: greenhouse\n    token: stripe\n")
    companies.add_company(cfile, "Stripe")
    companies.set_proposal(cfile, "Stripe", url="https://boards.greenhouse.io/stripe",
                           count=9, evidence="ev")
    companies.approve(cfile, sfile, "Stripe")
    from internshelper import config
    assert len(config.load_sources(sfile)[0]) == 1          # linked, not duplicated
    assert companies.load_companies(cfile)[0][0].board == "greenhouse:stripe"


def test_approve_requires_a_proposal(cfile, sfile):
    companies.add_company(cfile, "Plaid")
    with pytest.raises(ValueError, match="proposal"):
        companies.approve(cfile, sfile, "Plaid")


def test_reject_and_mark_no_board(cfile):
    companies.add_company(cfile, "TinyStartup")
    companies.set_proposal(cfile, "TinyStartup", url="https://x", count=0, evidence="ev")
    companies.reject(cfile, "TinyStartup", notes="wrong board")
    e = companies.load_companies(cfile)[0][0]
    assert e.status == "no-board" and e.proposal == {} and "wrong board" in e.notes


def test_resolve_write_validates_source_key(cfile, sfile):
    sfile.write_text("sources:\n  - type: greenhouse\n    token: stripe\n")
    companies.add_company(cfile, "Stripe")
    with pytest.raises(ValueError, match="not in sources"):
        companies.resolve_write(cfile, sfile, "Stripe", "greenhouse:nope")
    companies.resolve_write(cfile, sfile, "Stripe", "greenhouse:stripe")
    assert companies.load_companies(cfile)[0][0].status == "resolved"


def test_dangling_reports_broken_pointers():
    es = [CompanyEntry(name="A", status="resolved", board="greenhouse:gone"),
          CompanyEntry(name="B", status="resolved", board="greenhouse:ok")]
    assert companies.dangling(es, {"greenhouse:ok"}) == ["A"]


# ---------- CLI ----------

def test_cli_add_list_json(cfile, capsys):
    assert companies.main(["add", "Goldman Sachs"]) == 0
    assert companies.main(["list", "--json"]) == 0
    out = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert out == [{"name": "Goldman Sachs", "status": "pending", "board": "",
                    "notes": "", "proposal": {}}]


def test_cli_propose_approve_flow(cfile, sfile, capsys):
    companies.main(["add", "Stripe"])
    assert companies.main(["propose", "Stripe", "--url",
                           "https://boards.greenhouse.io/stripe",
                           "--count", "9", "--evidence", "careers page embeds it"]) == 0
    assert companies.main(["approve", "Stripe"]) == 0
    from internshelper import config
    assert [s.source_key for s in config.load_sources(sfile)[0]] == ["greenhouse:stripe"]


def test_cli_errors_exit_1(cfile, capsys):
    assert companies.main(["approve", "Nobody"]) == 1
    assert "unknown company" in capsys.readouterr().out
