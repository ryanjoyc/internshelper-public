"""Workday CXS connector tests — frozen fixtures, no live calls.

Fixtures are trimmed real captures from blueorigin.wd5.myworkdayjobs.com:
- workday_cxs_page1.json: 20 items, total rewritten to 25; item [3] hand-edited to lack
  `externalPath` (exercises skip+warn).
- workday_cxs_page2.json: 5 items, and — faithfully to the live API — `total: 0` (later
  pages really do report 0, which is why total must be read from the FIRST page only).
- workday_cxs_empty.json: an empty board.
"""

import json
from pathlib import Path

import httpx
import pytest

from internshelper.config import SourceEntry
from internshelper.connectors import build_connector
from internshelper.connectors.workday import (
    MAX_POSTINGS,
    WorkdayConnector,
    parse_board_url,
)

FX = Path(__file__).parent / "fixtures"
BOARD = "https://blueorigin.wd5.myworkdayjobs.com/BlueOrigin"


def _entry(**kw):
    return SourceEntry(type="workday", token=BOARD, label="Blue Origin", **kw)


def _pages():
    return [
        json.loads((FX / "workday_cxs_page1.json").read_text()),
        json.loads((FX / "workday_cxs_page2.json").read_text()),
    ]


# ---------- parse_board_url (pure) ----------

def test_parse_board_url_canonicalizes():
    b = parse_board_url("https://Mastercard.wd1.myworkdayjobs.com/CorporateCareers/")
    assert b.tenant == "mastercard"
    assert b.host == "mastercard.wd1.myworkdayjobs.com"
    assert b.site == "CorporateCareers"  # site case preserved (CXS paths are case-sensitive)
    assert b.base_url == "https://mastercard.wd1.myworkdayjobs.com/CorporateCareers"
    assert b.jobs_api == (
        "https://mastercard.wd1.myworkdayjobs.com/wday/cxs/mastercard/CorporateCareers/jobs"
    )


def test_parse_board_url_strips_locale_segment():
    a = parse_board_url("https://mastercard.wd1.myworkdayjobs.com/en-US/CorporateCareers")
    b = parse_board_url("https://mastercard.wd1.myworkdayjobs.com/CorporateCareers")
    assert a == b


def test_parse_board_url_resolves_single_job_link_to_board():
    b = parse_board_url(
        "https://mastercard.wd1.myworkdayjobs.com/en-US/CorporateCareers/job/"
        "Software-Engineer-Intern--Summer-2027_R-257373-2"
    )
    assert b.base_url == "https://mastercard.wd1.myworkdayjobs.com/CorporateCareers"


def test_parse_board_url_rejects_garbage():
    for bad in (
        "https://example.com/careers",
        "https://mastercard.wd1.myworkdayjobs.com",  # no site
        "https://mastercard.wd1.myworkdayjobs.com/",
        "not a url",
    ):
        with pytest.raises(ValueError):
            parse_board_url(bad)


# ---------- parse (pure, fixture-fed) ----------

def test_parse_maps_fields():
    c = build_connector(_entry())
    posts = c.parse(_pages())
    assert len(posts) == 24  # 25 items, one lacks externalPath -> skipped
    p = posts[0]
    assert p.title == "Ground System Design Engineer III"
    assert p.company == "Blue Origin"  # label fallback: CXS carries no company field
    assert p.url == BOARD + "/job/Space-Coast-FL/Ground-System-Design-Engineer-III_R68341"
    assert p.location == "3 Locations"
    assert p.description == ""  # no per-job detail fetches
    assert p.posted_at is None  # postedOn is relative prose; startDate is the POSTING date
    assert p.raw["bulletFields"] == ["R68341"]
    assert p.source_key == f"workday:{BOARD}"


def test_parse_posting_id_is_stable_and_tenant_scoped():
    c = build_connector(_entry())
    a = c.parse(_pages())
    b = c.parse(_pages())
    assert [p.posting_id for p in a] == [p.posting_id for p in b]
    ids = {p.posting_id for p in a}
    assert len(ids) == 24
    assert all(i.startswith("workday:blueorigin:") for i in ids)
    assert all(len(i.rsplit(":", 1)[1]) == 16 for i in ids)


def test_parse_skips_item_without_externalpath_with_diagnostic():
    c = build_connector(_entry())
    posts = c.parse(_pages())
    assert all("Shift Leader A shift" != p.title for p in posts)
    assert any("externalPath" in d for d in c.diagnostics)


def test_parse_empty_board():
    c = build_connector(_entry())
    assert c.parse([json.loads((FX / "workday_cxs_empty.json").read_text())]) == []


# ---------- fetch (pagination loop; _post_json monkeypatched) ----------

def _fetching(monkeypatch, pages, entry=None):
    """Wire a connector whose _post_json serves `pages` in order, recording call bodies."""
    c = build_connector(entry or _entry())
    calls = []

    def fake_post(url, body):
        calls.append((url, body))
        return pages[len(calls) - 1]

    monkeypatch.setattr(c, "_post_json", fake_post)
    return c, calls


def test_fetch_paginates_until_first_page_total(monkeypatch):
    c, calls = _fetching(monkeypatch, _pages())
    posts = c.fetch().postings
    assert len(posts) == 24
    assert [b["offset"] for _, b in calls] == [0, 20]
    assert all(b["limit"] == 20 for _, b in calls)
    assert all(b["searchText"] == "" for _, b in calls)
    assert all(u.endswith("/wday/cxs/blueorigin/BlueOrigin/jobs") for u, _ in calls)


def test_fetch_result_marks_a_malformed_skipped_record_partial(monkeypatch):
    connector, _calls = _fetching(monkeypatch, _pages())

    result = connector.fetch()

    assert result.complete is False
    assert len(result.postings) == 24


def test_fetch_result_marks_overlapping_pages_partial(monkeypatch):
    items = [
        {
            "title": f"Engineering Intern {index}",
            "externalPath": f"/job/City/Engineering-Intern-{index}_R-{index}",
        }
        for index in range(20)
    ]
    pages = [
        {"total": 21, "jobPostings": items},
        {"total": 0, "jobPostings": [items[0]]},
    ]
    connector, _calls = _fetching(monkeypatch, pages)

    result = connector.fetch()

    assert result.complete is False
    assert len({posting.posting_id for posting in result.postings}) == 20
    assert any("duplicate" in diagnostic for diagnostic in connector.diagnostics)


def test_fetch_result_without_first_page_total_is_partial(monkeypatch):
    connector, _calls = _fetching(
        monkeypatch,
        [{"jobPostings": [{"title": "Intern", "externalPath": "/job/Intern_R-1"}]}],
    )

    result = connector.fetch()

    assert result.complete is False
    assert len(result.postings) == 1
    assert any("total" in diagnostic for diagnostic in connector.diagnostics)


def test_fetch_passes_search_and_emits_visibility_diagnostic(monkeypatch):
    c, calls = _fetching(monkeypatch, _pages(), entry=_entry(search="intern"))
    c.fetch()
    assert all(b["searchText"] == "intern" for _, b in calls)
    assert any("search" in d and "intern" in d for d in c.diagnostics)


def test_fetch_raises_on_empty_page_before_total(monkeypatch):
    # Partial data must RAISE — close-detection would mass-close live postings otherwise.
    short = [_pages()[0], {"total": 0, "jobPostings": []}]
    c, _ = _fetching(monkeypatch, short)
    with pytest.raises(RuntimeError, match="partial"):
        c.fetch()


def test_fetch_raises_when_total_exceeds_cap(monkeypatch):
    big = dict(_pages()[0], total=MAX_POSTINGS + 1)
    c, _ = _fetching(monkeypatch, [big])
    with pytest.raises(RuntimeError, match="search"):
        c.fetch()  # actionable: suggests narrowing with `search:`


def test_fetch_4xx_raises_with_guidance(monkeypatch):
    c = build_connector(_entry())

    def boom(url, body):
        req = httpx.Request("POST", url)
        raise httpx.HTTPStatusError(
            "422", request=req, response=httpx.Response(422, request=req)
        )

    monkeypatch.setattr(c, "_post_json", boom)
    with pytest.raises(RuntimeError, match="site"):
        c.fetch()


def test_build_connector_resolves_workday():
    assert isinstance(build_connector(_entry()), WorkdayConnector)
