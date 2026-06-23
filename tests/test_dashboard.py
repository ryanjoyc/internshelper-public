"""Headless click-through of the Streamlit dashboard via AppTest.

Catches render/logic errors across every view — including the cross-thread crash class and
the earlier stale-install ImportError — that unit tests on the data layer can't see.
"""

import sqlite3
import tomllib
from pathlib import Path

import pytest
import streamlit.config as st_config
from streamlit.testing.v1 import AppTest

from internshelper import config, db, sniffer, sources, store
from internshelper.config import SourceEntry
from internshelper.models import Posting
from internshelper.review import set_verdict

DASH = "internshelper/dashboard.py"
REPO = Path(__file__).resolve().parents[1]


def _by_key(elements, key):
    for el in elements:
        if el.key == key:
            return el
    raise KeyError(key)


def _by_label(elements, label):
    for el in elements:
        if el.label == label:
            return el
    raise KeyError(label)


class _FakeConnector:
    def __init__(self, posts=None, exc=None, warnings=None):
        self._posts, self._exc = posts or [], exc
        # Push model: diagnostics read off the connector after fetch (matches the real
        # Connector.diagnostics channel that fetch_test now consumes).
        self.diagnostics = list(warnings or [])

    def fetch(self):
        if self._exc:
            raise self._exc
        return self._posts


def _wire(monkeypatch, posts=None, exc=None, warnings=None):
    monkeypatch.setattr(sources, "build_connector",
                        lambda entry: _FakeConnector(posts=posts, exc=exc, warnings=warnings))


def _dpost(pid, title):
    return Posting(posting_id=pid, source_key="greenhouse:stripe", title=title,
                   company="C", url=f"https://x/{pid}")


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    path = tmp_path / "d.db"
    monkeypatch.setenv("INTERNSHELPER_DB", str(path))
    c = db.connect(path)
    db.init_db(c)
    for i in range(3):
        store.upsert(
            c,
            Posting(posting_id=f"greenhouse:{i}", source_key="greenhouse:stripe",
                    title="Software Engineer Intern", company="Stripe", url=f"https://x/{i}",
                    is_cs_relevant=True, is_internship=True),
            now="2026-06-18T10:00:00+00:00",
        )
    set_verdict(c, "greenhouse:0", "match", "looks good", now="2026-06-18T11:00:00+00:00")
    c.close()
    return path


def _run():
    return AppTest.from_file(DASH, default_timeout=30).run()


def test_initial_render_no_exception(seeded_db):
    at = _run()
    assert not at.exception  # also covers Tracker + Health (st.tabs runs all bodies)


def test_every_feed_view_renders(seeded_db):
    at = _run()
    assert not at.exception
    for view in ["Pending review", "All postings", "Confirmed matches"]:
        at.radio[0].set_value(view).run()
        assert not at.exception, f"view {view!r} raised: {at.exception}"


def test_all_postings_search_box(seeded_db):
    at = _run()
    at.radio[0].set_value("All postings").run()
    at.text_input[0].set_value("software").run()
    assert not at.exception


def test_save_application_form_writes_row(seeded_db):
    at = _run()
    at.radio[0].set_value("Confirmed matches").run()
    assert not at.exception
    assert len(at.button) >= 1  # the Save form-submit button in the match's expander
    at.button[0].click().run()
    assert not at.exception
    c = sqlite3.connect(seeded_db)
    row = c.execute(
        "SELECT 1 FROM applications WHERE posting_id = 'greenhouse:0'"
    ).fetchone()
    assert row is not None  # the form wrote an application row


# --- Sources tab (item 2) --------------------------------------------------------

@pytest.fixture
def sources_file(tmp_path, monkeypatch):
    p = tmp_path / "sources.yaml"
    p.write_text("sources:\n")
    monkeypatch.setenv("INTERNSHELPER_SOURCES", str(p))
    return p


def test_sources_tab_renders(seeded_db, sources_file):
    at = _run()
    assert not at.exception  # st.tabs runs the Sources body too


def test_sources_tab_lists_existing(seeded_db, sources_file):
    sources_file.write_text("sources:\n  - type: greenhouse\n    token: stripe\n")
    at = _run()
    assert not at.exception
    sel = _by_key(at.selectbox, "src-remove-select")
    assert "greenhouse:stripe" in sel.options


def test_add_source_detect_then_confirm_writes(seeded_db, sources_file, monkeypatch):
    _wire(monkeypatch, posts=[_dpost("1", "SWE Intern")])
    at = _run()
    _by_key(at.text_input, "src-url").set_value("https://boards.greenhouse.io/stripe")
    _by_label(at.button, "Detect & test").click()
    at.run()
    assert not at.exception
    _by_label(at.button, "Confirm & add").click()
    at.run()
    assert not at.exception
    keys = [e.source_key for e in config.load_sources(sources_file)[0]]
    assert "greenhouse:stripe" in keys


def test_add_source_unknown_host_shows_error(seeded_db, sources_file, monkeypatch):
    monkeypatch.setattr(sniffer, "sniff_careers_page", lambda url, label=None: [])
    at = _run()
    _by_key(at.text_input, "src-url").set_value("https://mycorp.com/careers")
    _by_label(at.button, "Detect & test").click()
    at.run()
    assert not at.exception
    assert len(at.error) >= 1  # detection failure surfaced, no embedded board found
    assert config.load_sources(sources_file)[0] == []  # nothing written


def test_add_source_sniffer_candidate_flow(seeded_db, sources_file, monkeypatch):
    monkeypatch.setattr(sniffer, "sniff_careers_page",
                        lambda url, label=None: [SourceEntry(type="greenhouse", token="stripe")])
    _wire(monkeypatch, posts=[_dpost("1", "SWE Intern")])
    at = _run()
    _by_key(at.text_input, "src-url").set_value("https://mycorp.com/careers")
    _by_label(at.button, "Detect & test").click()
    at.run()
    assert not at.exception
    _by_label(at.button, "Use this board").click()
    at.run()
    _by_label(at.button, "Confirm & add").click()
    at.run()
    assert not at.exception
    keys = [e.source_key for e in config.load_sources(sources_file)[0]]
    assert "greenhouse:stripe" in keys


def test_add_source_duplicate_blocks(seeded_db, sources_file, monkeypatch):
    sources_file.write_text("sources:\n  - type: greenhouse\n    token: stripe\n")
    _wire(monkeypatch, posts=[_dpost("1", "SWE Intern")])
    at = _run()
    _by_key(at.text_input, "src-url").set_value("https://boards.greenhouse.io/stripe")
    _by_label(at.button, "Detect & test").click()
    at.run()
    assert not at.exception
    assert len(at.warning) >= 1
    assert len(config.load_sources(sources_file)[0]) == 1  # no duplicate appended


# --- Pending review UI (item 1) --------------------------------------------------

@pytest.fixture
def review_db(tmp_path, monkeypatch):
    path = tmp_path / "r.db"
    monkeypatch.setenv("INTERNSHELPER_DB", str(path))
    payloads = tmp_path / "payloads"
    c = db.connect(path)
    db.init_db(c)
    store.upsert(c, Posting(posting_id="greenhouse:cand", source_key="greenhouse:stripe",
                            title="SWE Intern", company="Stripe", url="https://x/cand",
                            is_cs_relevant=True, is_internship=True,
                            raw={"content": "<p>Build <b>backend</b> systems.</p>"}),
                 "2026-06-18T10:00:00+00:00", payloads_dir=str(payloads))
    store.upsert(c, Posting(posting_id="greenhouse:nopay", source_key="greenhouse:stripe",
                            title="Data Intern", company="Stripe", url="https://x/nopay",
                            is_cs_relevant=True, is_internship=True),
                 "2026-06-18T10:00:00+00:00", payloads_dir=str(payloads))
    store.upsert(c, Posting(posting_id="greenhouse:noncand", source_key="greenhouse:stripe",
                            title="Barista", company="Stripe", url="https://x/non"),
                 "2026-06-18T10:00:00+00:00", payloads_dir=str(payloads))
    c.close()
    return path


def test_pending_review_marks_match_writes_verdict(review_db):
    at = _run()
    at.radio[0].set_value("Pending review").run()
    _by_key(at.button, "match-greenhouse:cand").click()
    at.run()
    assert not at.exception
    c = sqlite3.connect(review_db)
    row = c.execute("SELECT verdict, review_status FROM postings "
                    "WHERE posting_id='greenhouse:cand'").fetchone()
    assert row == ("match", "reviewed")


def test_pending_review_no_match_writes_verdict(review_db):
    at = _run()
    at.radio[0].set_value("Pending review").run()
    _by_key(at.button, "nomatch-greenhouse:noncand").click()
    at.run()
    assert not at.exception
    c = sqlite3.connect(review_db)
    row = c.execute("SELECT verdict, review_status FROM postings "
                    "WHERE posting_id='greenhouse:noncand'").fetchone()
    assert row == ("no_match", "reviewed")


def test_pending_peek_renders_both_payload_branches(review_db):
    # Exercises the payload-present (st.json) and missing-payload (caption) expander paths.
    at = _run()
    at.radio[0].set_value("Pending review").run()
    assert not at.exception


def test_bulk_clear_non_candidates(review_db):
    at = _run()
    at.radio[0].set_value("Pending review").run()
    _by_key(at.button, "bulk-start").click()
    at.run()
    _by_key(at.button, "bulk-yes").click()
    at.run()
    assert not at.exception
    c = sqlite3.connect(review_db)
    non = c.execute("SELECT review_status FROM postings "
                    "WHERE posting_id='greenhouse:noncand'").fetchone()[0]
    cand = c.execute("SELECT review_status FROM postings "
                     "WHERE posting_id='greenhouse:cand'").fetchone()[0]
    assert non == "reviewed"   # non-candidate cleared
    assert cand == "pending"   # candidate left for manual review


def test_finish_resets_flag_when_queue_empties(review_db):
    c = db.connect(review_db)
    db.set_meta(c, "pending_notified", "1")
    c.close()
    at = _run()
    at.radio[0].set_value("Pending review").run()
    for pid in ["greenhouse:cand", "greenhouse:nopay", "greenhouse:noncand"]:
        _by_key(at.button, f"nomatch-{pid}").click()
        at.run()
    c = db.connect(review_db)
    assert db.get_meta(c, "pending_notified") == "0"


# --- UI theming guards (sleek/modern dark refit) ---------------------------------

def _theme_block() -> dict:
    with open(REPO / ".streamlit" / "config.toml", "rb") as fh:
        return tomllib.load(fh)["theme"]


def test_config_toml_theme_keys_are_all_valid():
    """Every [theme] key must be a real Streamlit theme option (guards typos)."""
    theme = _theme_block()
    # top-level theme option names from the installed Streamlit (e.g. 'primaryColor',
    # 'base', 'fontFaces') — exclude the nested theme.dark.* / theme.sidebar.* tables.
    valid = {
        name.split(".", 1)[1]
        for name in st_config._config_options_template
        if name.startswith("theme.") and name.count(".") == 1
    }
    unknown = set(theme) - valid
    assert not unknown, f"invalid [theme] keys: {unknown}"
    # the locked design decisions
    assert theme["base"] == "dark"
    assert theme["primaryColor"] == "#6b78ff"


def test_geist_fonts_present_and_referenced():
    """The 3 Geist woff2 are committed and every fontFace url resolves to one.

    Streamlit serves `static/` relative to the entrypoint's dir (internshelper/), exposed at
    `app/static/*` — so the files live in internshelper/static/, not the repo root.
    """
    fonts_dir = REPO / "internshelper" / "static"
    for w in (500, 600, 700):
        assert (fonts_dir / f"geist-{w}.woff2").is_file()
    faces = _theme_block()["fontFaces"]
    assert len(faces) == 3
    for face in faces:
        assert face["family"] == "Geist"
        # 'app/static/x' is served from <entrypoint dir>/static/x == internshelper/static/x
        rel = face["url"].removeprefix("app/")
        assert (REPO / "internshelper" / rel).is_file(), f"font url unresolved: {face['url']}"


def test_dashboard_injects_custom_css(seeded_db):
    at = _run()
    assert not at.exception
    assert any("<style" in m.value for m in at.markdown), "no <style> block was injected"
