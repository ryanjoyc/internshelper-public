"""Web UI: app factory, page smoke tests, static assets.

Shared fixtures (seeded_db / sources_file / client) live in conftest.py. Interaction
tests live in test_web_triage.py / test_web_board.py / test_web_sources.py; this file
covers the skeleton: every page renders, /healthz answers, vendored assets are served.
"""

import pytest
from fastapi.testclient import TestClient


def test_healthz_returns_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.text == "ok"


@pytest.mark.parametrize("path", ["/review", "/board", "/postings", "/health", "/sources"])
def test_every_page_renders(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert "internsHELPer" in r.text


def test_root_redirects_to_review_when_pending(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/review"


def test_root_redirects_to_board_when_queue_clear(tmp_path, monkeypatch, sources_file):
    path = tmp_path / "empty.db"
    monkeypatch.setenv("INTERNSHELPER_DB", str(path))
    from internshelper.web import create_app

    with TestClient(create_app()) as c:
        r = c.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/board"


def test_review_page_lists_pending(client):
    r = client.get("/review")
    assert "Software Engineer Intern" in r.text
    assert "Stripe" in r.text


def test_board_page_shows_match(client):
    r = client.get("/board")
    assert "Stripe" in r.text  # greenhouse:0 is a confirmed match


def test_sources_page_lists_existing(client, sources_file):
    sources_file.write_text("sources:\n  - type: greenhouse\n    token: stripe\n")
    r = client.get("/sources")
    assert "greenhouse:stripe" in r.text


def test_static_assets_served(client):
    for path in (
        "/static/fonts/geist-500.woff2",
        "/static/fonts/geist-600.woff2",
        "/static/fonts/geist-700.woff2",
        "/static/vendor/htmx.min.js",
        "/static/vendor/alpine.min.js",
        "/static/vendor/sortable.min.js",
        "/static/css/app.css",
        "/static/js/app.js",
    ):
        assert client.get(path).status_code == 200, path


def test_api_docs_disabled(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
