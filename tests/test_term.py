"""Tier-0 term-classifier accuracy + the classify pass.

Cases capture each rubric branch and are drawn from the real manual run over speedyapply
(see the run's PROCESS.md): explicit target, explicit other term, co-op date ranges, CY
notation, bare cycle year, undated intern, no-description, and the new-grad term-mismatch.
"""

from __future__ import annotations

import pytest

from internshelper import db, store, term
from internshelper.models import Posting

TARGET = ("summer", 2027)

# (title, description, term_eligible, expected_verdict)
CASES = [
    ("explicit-target",     "Software Engineer Intern", "Join us Summer 2027!", True, "EXPLICIT"),
    ("explicit-year-first", "Intern", "2027 Summer internship program", True, "EXPLICIT"),
    ("explicit-apostrophe", "Intern", "Summer '27 cohort starts soon", True, "EXPLICIT"),
    ("start-date-in-target","Intern", "Internship begins June 2027", True, "EXPLICIT"),
    ("coop-range-fall",     "SWE Intern", "6-month program: October 5, 2026 - April 2, 2027", True, "NOT"),
    ("coop-aug-2026",       "Co-op", "Co-op position starting August 17, 2026", True, "NOT"),
    ("explicit-fall-2026",  "Co-Op", "for the September 2026 - December 2026 co-op term", True, "NOT"),
    ("cy26-autumn",         "FPE CE Backend Intern For CY26 Autumn Semeter", "", True, "NOT"),
    ("bare-2026-cycle",     "Software Intern", "Apply to our 2026 internship cohort", True, "NOT"),
    ("undated-intern",      "Software Intern", "As a Software Intern you will build things", True, "POSSIBLE"),
    ("summer-no-year",      "Intern", "What are you doing this summer? Join us.", True, "POSSIBLE"),
    ("no-description",      "Software Intern", "", True, "UNREADABLE"),
    ("newgrad-ineligible",  "Software Engineer - 2027 University Grad", "", False, "NOT"),
    ("target-year-and-season", "Intern", "Our 2027 cohort; a summer experience awaits", True, "LIKELY"),
]


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_classify_text(case):
    _id, title, desc, elig, expected = case
    v = term.classify_text(title, desc, TARGET, term_eligible=elig)
    assert v.verdict == expected, f"{_id}: got {v.verdict} ({v.evidence})"


def test_explicit_sets_season_year():
    v = term.classify_text("Intern", "Summer 2027 program", TARGET, term_eligible=True)
    assert (v.season, v.year) == ("summer", 2027)


def test_target_parsing():
    assert term.parse_target("summer:2027") == ("summer", 2027)
    assert term.parse_target("fall 2026") == ("fall", 2026)
    assert term.parse_target("Spring-2028") == ("spring", 2028)
    with pytest.raises(ValueError):
        term.parse_target("nonsense")


def test_autumn_normalizes_to_fall():
    sig = term.parse_term("Autumn 2026 internship")
    assert ("fall", 2026) in sig.season_years


def test_bare_year_window_ignores_ancient_years():
    # A "founded in 1998" year must not be read as a cycle signal.
    v = term.classify_text("Intern", "Acme, founded in 1998, seeks an intern", TARGET, term_eligible=True)
    assert v.verdict == "POSSIBLE"


def _posting(pid, title, desc="", url="", is_intern=1, is_newgrad=0):
    return Posting(
        posting_id=pid, source_key="markdown:x", title=title, company="C",
        url=url, description=desc, is_internship=bool(is_intern), is_newgrad=bool(is_newgrad),
    )


def test_classify_pass_writes_verdicts_and_enriches(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    # one with an inline description, one description-less (will be "enriched" via injected get)
    store.upsert(conn, _posting("a", "Software Intern", "Summer 2027 cohort"), now=term.clock.now_iso())
    store.upsert(conn, _posting("b", "Software Intern", "",
                                url="https://job-boards.greenhouse.io/acme/jobs/123"),
                 now=term.clock.now_iso())
    store.upsert(conn, _posting("c", "New Grad SWE", "", is_intern=0, is_newgrad=1), now=term.clock.now_iso())

    def fake_get(_api_url):  # pretend the ATS returned an explicit-2026 JD
        return {"content": "This is our Fall 2026 internship."}

    tally = term.classify_pass(conn, TARGET, get_json=fake_get)
    assert tally["enriched"] == 1            # only the description-less 'b' was fetched
    rows = {r["posting_id"]: r for r in conn.execute(
        "SELECT posting_id, term_verdict, term_season, term_year FROM postings")}
    assert rows["a"]["term_verdict"] == "EXPLICIT"
    assert rows["b"]["term_verdict"] == "NOT"      # enriched JD said Fall 2026
    assert rows["c"]["term_verdict"] == "NOT"      # new-grad, term-ineligible


def test_classify_pass_skips_already_classified(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    store.upsert(conn, _posting("a", "Intern", "Summer 2027"), now=term.clock.now_iso())
    term.classify_pass(conn, TARGET)
    # second pass without --reclassify should classify zero new rows
    tally = term.classify_pass(conn, TARGET)
    assert sum(tally[v] for v in term.TERM_VERDICTS) == 0


def test_list_candidates_only_ambiguous(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    store.upsert(conn, _posting("a", "Intern", "Summer 2027"), now=term.clock.now_iso())   # EXPLICIT
    store.upsert(conn, _posting("b", "Intern", "great team"), now=term.clock.now_iso())     # POSSIBLE
    term.classify_pass(conn, TARGET)
    cand = term.list_candidates(conn)
    assert [c["posting_id"] for c in cand] == ["b"]
