import json
from pathlib import Path

import pytest

from internshelper import classify
from internshelper.models import Posting

FIXTURES = Path(__file__).parent / "fixtures" / "labeled_postings.json"
LABELED = json.loads(FIXTURES.read_text())


def _posting(title, description=""):
    return Posting(
        posting_id="t:1", source_key="greenhouse:x",
        title=title, company="C", url="u", description=description,
    )


@pytest.mark.parametrize("case", LABELED, ids=[c["id"] for c in LABELED])
def test_labeled_fixtures_classify_exactly(case):
    compiled = classify.compile_keywords(classify.DEFAULT_KEYWORDS)
    p = classify.classify(_posting(case["title"], case["description"]), compiled)
    got = {
        "is_internship": p.is_internship,
        "is_newgrad": p.is_newgrad,
        "is_cs_relevant": p.is_cs_relevant,
    }
    assert got == case["expected"], case.get("note", "")


def test_classification_is_config_driven_not_hardcoded():
    # With a custom keyword set that lacks 'software'/'engineer', a normally-CS
    # title must NOT be flagged CS — proving classify reads the passed keywords.
    custom = {"internship": ["intern"], "newgrad": ["new grad"], "cs": ["rust"]}
    compiled = classify.compile_keywords(custom)
    p = classify.classify(_posting("Software Engineer Intern"), compiled)
    assert p.is_internship is True
    assert p.is_cs_relevant is False
    p2 = classify.classify(_posting("Rust Intern"), compiled)
    assert p2.is_cs_relevant is True


def test_summer_year_pattern_boundaries():
    compiled = classify.compile_keywords(classify.DEFAULT_KEYWORDS)
    assert classify.classify(_posting("Summer 2026 Analyst"), compiled).is_internship is True
    assert classify.classify(_posting("Summer Analyst"), compiled).is_internship is False
    assert classify.classify(_posting("Summer 20 Special"), compiled).is_internship is False


def test_short_acronyms_respect_word_boundaries():
    compiled = classify.compile_keywords(classify.DEFAULT_KEYWORDS)
    assert classify.classify(_posting("AI Engineer"), compiled).is_cs_relevant is True
    assert classify.classify(_posting("Available Now"), compiled).is_cs_relevant is False
