import json
from pathlib import Path

from internshelper.config import SourceEntry
from internshelper.connectors import build_connector

FX = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FX / name).read_text())


def test_greenhouse_parse():
    c = build_connector(SourceEntry(type="greenhouse", token="stripe", label="Stripe"))
    posts = c.parse(_load("greenhouse_stripe.json"))
    assert len(posts) == 2
    p = posts[0]
    assert p.posting_id == "greenhouse:7954688"
    assert p.source_key == "greenhouse:stripe"
    assert p.title == "Account Executive, AI Sales (Grower)"
    assert p.company == "Stripe"  # greenhouse provides company_name
    assert p.url == "https://stripe.com/jobs/search?gh_jid=7954688"
    assert p.location == "San Francisco, CA"
    assert p.description  # the content HTML is retained


def test_lever_parse():
    c = build_connector(SourceEntry(type="lever", token="leverdemo"))
    posts = c.parse(_load("lever_leverdemo.json"))
    p = posts[0]
    assert p.posting_id == "lever:33538a2f-d27d-4a96-8f05-fa4b0e4d940e"
    assert p.source_key == "lever:leverdemo"
    assert p.title == "AbelsonTaylor Writer"
    assert p.url.startswith("https://jobs.lever.co/leverdemo/")
    assert p.location == "Arlington, TX"
    assert p.company == "leverdemo"  # no label -> falls back to token
    assert p.description


def test_ashby_parse():
    c = build_connector(SourceEntry(type="ashby", token="ramp", label="Ramp"))
    posts = c.parse(_load("ashby_ramp.json"))
    p = posts[0]
    assert p.posting_id == "ashby:34413f8d-26bf-4bbc-8ade-eb309a0e2245"
    assert p.source_key == "ashby:ramp"
    assert p.title == "Security Engineer, Cloud"  # leading whitespace stripped
    assert p.company == "Ramp"
    assert p.url.startswith("https://jobs.ashbyhq.com/ramp/")
    assert p.location == "New York, NY (HQ)"
    assert p.description


def test_github_parse_filters_inactive_and_keys_on_id():
    e = SourceEntry(type="github", token="https://example.com/listings.json", label="Simplify")
    posts = build_connector(e).parse(_load("github_listings.json"))
    ids = [p.posting_id for p in posts]
    assert len(posts) == 2  # the inactive Experian listing is dropped
    assert "github:6539aef4-45c3-4158-b5c4-ebb8f7fa8624" in ids
    assert "github:d9d3656e-d93b-4c07-971b-af063e953961" in ids
    assert all("fb9ec36c" not in i for i in ids)
    bloom = next(p for p in posts if p.posting_id.endswith("d9d3656e-d93b-4c07-971b-af063e953961"))
    assert bloom.company == "Bloom Energy"
    assert bloom.title == "Full Stack Developer Intern"
    assert bloom.location == "San Jose, CA"
    assert bloom.source_key == "github:https://example.com/listings.json"


def test_connectors_carry_the_raw_source_item():
    gh = build_connector(SourceEntry(type="greenhouse", token="stripe")).parse(
        _load("greenhouse_stripe.json")
    )
    assert gh[0].raw is not None and gh[0].raw["id"] == 7954688
    lv = build_connector(SourceEntry(type="lever", token="leverdemo")).parse(
        _load("lever_leverdemo.json")
    )
    assert lv[0].raw is not None and lv[0].raw["id"] == "33538a2f-d27d-4a96-8f05-fa4b0e4d940e"
    ab = build_connector(SourceEntry(type="ashby", token="ramp")).parse(_load("ashby_ramp.json"))
    assert ab[0].raw is not None and ab[0].raw["id"] == "34413f8d-26bf-4bbc-8ade-eb309a0e2245"
    gj = build_connector(SourceEntry(type="github", token="https://x/listings.json")).parse(
        _load("github_listings.json")
    )
    assert gj[0].raw is not None and "id" in gj[0].raw


def test_connectors_extract_posted_at():
    gh = build_connector(SourceEntry(type="greenhouse", token="stripe")).parse(
        _load("greenhouse_stripe.json"))
    assert gh[0].posted_at.startswith("2026-06-02")  # first_published -04:00 -> UTC same day
    lv = build_connector(SourceEntry(type="lever", token="leverdemo")).parse(
        _load("lever_leverdemo.json"))
    assert lv[0].posted_at.startswith("2019-")  # createdAt epoch ms
    ab = build_connector(SourceEntry(type="ashby", token="ramp")).parse(_load("ashby_ramp.json"))
    assert ab[0].posted_at.startswith("2026-04-07")  # publishedAt
    gj = build_connector(SourceEntry(type="github", token="x")).parse(_load("github_listings.json"))
    assert gj[0].posted_at  # date_posted epoch s


def test_build_connector_rejects_unknown_type():
    import pytest
    with pytest.raises(ValueError):
        build_connector(SourceEntry(type="martian", token="x"))
