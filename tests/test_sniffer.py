"""Unit tests for the no-AI ATS sniffer.

Pure HTML scanning (`find_boards_in_html`) is tested against frozen snippets; the network
path (`sniff_careers_page`) is tested with a monkeypatched `httpx.get` — no real requests.
"""

import httpx
import pytest

from internshelper import sniffer


def _keys(entries):
    return sorted(e.source_key for e in entries)


# ---------- pure HTML scan ----------

def test_find_greenhouse_iframe_embed():
    html = '<iframe src="https://boards.greenhouse.io/embed/job_board?for=stripe"></iframe>'
    assert _keys(sniffer.find_boards_in_html(html)) == ["greenhouse:stripe"]


def test_find_greenhouse_plain_and_api_dedup():
    html = ('<a href="https://boards.greenhouse.io/stripe">jobs</a>'
            '<script>fetch("https://boards-api.greenhouse.io/v1/boards/stripe/jobs")</script>')
    assert _keys(sniffer.find_boards_in_html(html)) == ["greenhouse:stripe"]


def test_find_greenhouse_job_boards_host():
    html = '<a href="https://job-boards.greenhouse.io/acme">careers</a>'
    assert _keys(sniffer.find_boards_in_html(html)) == ["greenhouse:acme"]


def test_find_lever():
    html = '<a href="https://jobs.lever.co/netflix/">open roles</a>'
    assert _keys(sniffer.find_boards_in_html(html)) == ["lever:netflix"]


def test_find_ashby_preserves_case():
    html = '<script src="https://jobs.ashbyhq.com/Ramp/embed"></script>'
    entries = sniffer.find_boards_in_html(html)
    assert [e.token for e in entries] == ["Ramp"]  # case preserved
    assert entries[0].source_key == "ashby:Ramp"


def test_stoplist_filters_scaffolding_tokens():
    # 'embed' as a bare path segment must not become a token.
    html = '<iframe src="https://boards.greenhouse.io/embed"></iframe>'
    assert sniffer.find_boards_in_html(html) == []


def test_multiple_distinct_boards():
    html = ('https://boards.greenhouse.io/stripe '
            'https://jobs.lever.co/netflix '
            'https://jobs.ashbyhq.com/ramp')
    assert _keys(sniffer.find_boards_in_html(html)) == [
        "ashby:ramp", "greenhouse:stripe", "lever:netflix"]


def test_no_boards_returns_empty():
    assert sniffer.find_boards_in_html("<html><body>nothing here</body></html>") == []


def test_label_defaults_humanize_token():
    [e] = sniffer.find_boards_in_html("https://boards.greenhouse.io/big-corp")
    assert e.label == "Big Corp"


# ---------- network path ----------

class _Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


def test_sniff_careers_page_fetches_and_scans(monkeypatch):
    monkeypatch.setattr(sniffer.httpx, "get",
                        lambda *a, **k: _Resp('<a href="https://jobs.lever.co/foo">x</a>'))
    assert _keys(sniffer.sniff_careers_page("https://foo.com/careers")) == ["lever:foo"]


def test_sniff_careers_page_raises_on_http_error(monkeypatch):
    def _boom(*a, **k):
        raise httpx.ConnectError("boom")
    monkeypatch.setattr(sniffer.httpx, "get", _boom)
    with pytest.raises(sniffer.SnifferError):
        sniffer.sniff_careers_page("https://foo.com/careers")


def test_finds_workday_board_and_dedupes_locale_variants():
    # Real-shaped apply links (the md_vanshb03 fixture carries the same host style).
    html = (
        '<a href="https://blueorigin.wd5.myworkdayjobs.com/BlueOrigin/job/Seattle/X_R1">a</a>'
        '<a href="https://blueorigin.wd5.myworkdayjobs.com/en-US/BlueOrigin">apply</a>'
    )
    boards = sniffer.find_boards_in_html(html)
    assert [ (b.type, b.token) for b in boards ] == [
        ("workday", "https://blueorigin.wd5.myworkdayjobs.com/BlueOrigin"),
    ]


def test_workday_sniff_skips_siteless_urls():
    assert sniffer.find_boards_in_html('src="https://x.wd1.myworkdayjobs.com/"') == []
