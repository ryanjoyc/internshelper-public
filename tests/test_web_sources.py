"""Sources wizard: detect -> (candidates) -> preview -> add, plus remove.

Mirrors the CLI semantics the old dashboard pinned: duplicate check before the
fetch-test, sniffer fallback on undetectable URLs, the "Add anyway" force gate on
empty/degenerate fetches, and a hard stop (no confirm) on fetch errors.
"""

from internshelper import config, sniffer, sources
from internshelper.models import Posting


class _FakeConnector:
    def __init__(self, posts=None, exc=None, warnings=None):
        self._posts, self._exc, self._warnings = posts or [], exc, warnings or []

    def fetch(self):
        if self._exc:
            raise self._exc
        return self._posts

    def parse_warnings(self):
        return self._warnings


def _wire(monkeypatch, posts=None, exc=None, warnings=None):
    monkeypatch.setattr(
        sources, "build_connector",
        lambda entry: _FakeConnector(posts=posts, exc=exc, warnings=warnings),
    )


def _post(pid, title):
    return Posting(posting_id=pid, source_key="greenhouse:stripe", title=title,
                   company="C", url=f"https://x/{pid}")


def _keys(path):
    return [e.source_key for e in config.load_sources(path)[0]]


def test_detect_then_confirm_writes_source(client, sources_file, monkeypatch):
    _wire(monkeypatch, posts=[_post("greenhouse:1", "SWE Intern")])
    r = client.post("/sources/detect", data={"url": "https://boards.greenhouse.io/stripe",
                                             "label": "", "tmm": "", "cols": ""})
    assert r.status_code == 200
    assert "greenhouse:stripe" in r.text
    assert "Confirm &amp; add" in r.text
    assert "SWE Intern" in r.text  # fetch-test titles shown

    r2 = client.post("/sources/add", data={"type": "greenhouse", "token": "stripe",
                                           "label": "", "tmm": "", "cols": "", "force": "0"})
    assert r2.status_code == 200
    assert _keys(sources_file) == ["greenhouse:stripe"]


def test_detect_unknown_host_shows_error_and_writes_nothing(client, sources_file, monkeypatch):
    monkeypatch.setattr(sniffer, "sniff_careers_page",
                        lambda url, label=None: (_ for _ in ()).throw(sniffer.SnifferError("no boards")))
    r = client.post("/sources/detect", data={"url": "https://example.com/careers",
                                             "label": "", "tmm": "", "cols": ""})
    assert r.status_code == 200
    assert "Couldn" in r.text  # "Couldn't detect a source…"
    assert _keys(sources_file) == []


def test_sniffer_candidate_flow(client, sources_file, monkeypatch):
    cand = config.SourceEntry(type="greenhouse", token="acme")
    monkeypatch.setattr(sniffer, "sniff_careers_page", lambda url, label=None: [cand])
    _wire(monkeypatch, posts=[_post("greenhouse:1", "Intern")])

    r = client.post("/sources/detect", data={"url": "https://acme.com/careers",
                                             "label": "", "tmm": "intern", "cols": ""})
    assert "Use this board" in r.text
    assert "greenhouse:acme" in r.text

    r2 = client.post("/sources/candidate", data={"choice": "greenhouse:acme",
                                                 "label": "", "tmm": "intern", "cols": ""})
    assert "Confirm &amp; add" in r2.text

    r3 = client.post("/sources/add", data={"type": "greenhouse", "token": "acme",
                                           "label": "", "tmm": "intern", "cols": "", "force": "0"})
    assert r3.status_code == 200
    entries, _ = config.load_sources(sources_file)
    assert entries[0].source_key == "greenhouse:acme"
    assert entries[0].title_must_match == ["intern"]


def test_duplicate_blocks_before_fetch(client, sources_file, monkeypatch):
    sources_file.write_text("sources:\n  - type: greenhouse\n    token: stripe\n")
    _wire(monkeypatch, exc=RuntimeError("fetch should not run for duplicates"))
    r = client.post("/sources/detect", data={"url": "https://boards.greenhouse.io/stripe",
                                             "label": "", "tmm": "", "cols": ""})
    assert r.status_code == 200
    assert "Already present" in r.text
    assert len(_keys(sources_file)) == 1


def test_add_recheck_blocks_duplicate_race(client, sources_file):
    sources_file.write_text("sources:\n  - type: greenhouse\n    token: stripe\n")
    r = client.post("/sources/add", data={"type": "greenhouse", "token": "stripe",
                                          "label": "", "tmm": "", "cols": "", "force": "0"})
    assert r.status_code == 200
    assert "Already present" in r.text
    assert len(_keys(sources_file)) == 1


def test_empty_fetch_needs_force_gate(client, sources_file, monkeypatch):
    _wire(monkeypatch, posts=[], warnings=["no rows parsed"])
    r = client.post("/sources/detect", data={"url": "https://boards.greenhouse.io/stripe",
                                             "label": "", "tmm": "", "cols": ""})
    assert "Add anyway" in r.text
    assert 'name="force" value="1"' in r.text
    assert "no rows parsed" in r.text


def test_fetch_error_disables_confirm(client, sources_file, monkeypatch):
    _wire(monkeypatch, exc=RuntimeError("boom"))
    r = client.post("/sources/detect", data={"url": "https://boards.greenhouse.io/stripe",
                                             "label": "", "tmm": "", "cols": ""})
    assert "Fetch failed" in r.text
    assert "disabled" in r.text
    assert "Add anyway" not in r.text


def test_remove_source(client, sources_file):
    sources_file.write_text("sources:\n  - type: greenhouse\n    token: stripe\n")
    r = client.post("/sources/remove", data={"source_key": "greenhouse:stripe"})
    assert r.status_code == 200
    assert _keys(sources_file) == []
