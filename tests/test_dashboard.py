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

from internshelper import db, store
from internshelper.models import Posting
from internshelper.review import set_verdict

DASH = "internshelper/dashboard.py"
REPO = Path(__file__).resolve().parents[1]


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
