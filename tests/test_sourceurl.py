"""Unit tests for pure URL -> SourceEntry detection (no network).

`detect_source` maps a pasted job-board URL to a ready-to-write SourceEntry by hostname,
or raises SourceDetectionError for anything it can't resolve from the URL alone (a bare
company name / careers page / bare repo is the /add-source agent skill's job).
"""

import pytest

from internshelper import sourceurl


def d(url, **kw):
    return sourceurl.detect_source(url, **kw)


def test_greenhouse_boards_host():
    e = d("https://boards.greenhouse.io/stripe")
    assert e.type == "greenhouse" and e.token == "stripe"
    assert e.source_key == "greenhouse:stripe"
    assert e.label == "Stripe"


def test_greenhouse_job_boards_host_with_query():
    e = d("https://job-boards.greenhouse.io/stripe/?gh_src=abc")
    assert e.source_key == "greenhouse:stripe"


def test_greenhouse_trailing_slash():
    assert d("https://boards.greenhouse.io/stripe/").token == "stripe"


def test_schemeless_url_accepted():
    assert d("boards.greenhouse.io/stripe").source_key == "greenhouse:stripe"


def test_lever_board():
    assert d("https://jobs.lever.co/leverdemo").source_key == "lever:leverdemo"


def test_lever_single_job_link_yields_board_token():
    e = d("https://jobs.lever.co/leverdemo/12345678-aaaa-bbbb-cccc")
    assert e.source_key == "lever:leverdemo"


def test_ashby_preserves_case():
    e = d("https://jobs.ashbyhq.com/Ramp")
    assert e.type == "ashby" and e.token == "Ramp"
    assert e.source_key == "ashby:Ramp"


def test_github_blob_readme_becomes_markdown_raw():
    e = d("https://github.com/sndsh404/summer-2027-internships/blob/main/README.md")
    assert e.type == "markdown"
    assert e.token == (
        "https://raw.githubusercontent.com/sndsh404/summer-2027-internships/main/README.md"
    )


def test_github_blob_listings_json_becomes_github_raw():
    e = d("https://github.com/SimplifyJobs/Summer2025-Internships/blob/dev/.github/scripts/listings.json")
    assert e.type == "github"
    assert e.token == (
        "https://raw.githubusercontent.com/SimplifyJobs/Summer2025-Internships/dev/.github/scripts/listings.json"
    )


def test_raw_readme_is_markdown():
    url = "https://raw.githubusercontent.com/x/y/main/README.md"
    e = d(url)
    assert e.type == "markdown" and e.token == url


def test_raw_listings_json_is_github():
    url = "https://raw.githubusercontent.com/x/y/dev/listings.json"
    e = d(url)
    assert e.type == "github" and e.token == url


def test_raw_url_trailing_slash_normalized():
    e = d("https://raw.githubusercontent.com/x/y/main/README.md/")
    assert e.token == "https://raw.githubusercontent.com/x/y/main/README.md"  # no trailing slash


def test_raw_unknown_file_extension_raises():
    with pytest.raises(sourceurl.SourceDetectionError):
        d("https://raw.githubusercontent.com/x/y/main/data.txt")


def test_bare_github_repo_raises():
    with pytest.raises(sourceurl.SourceDetectionError):
        d("https://github.com/sndsh404/summer-2027-internships")


def test_unknown_host_raises_with_hint():
    with pytest.raises(sourceurl.SourceDetectionError) as ei:
        d("https://mycorp.com/careers")
    assert "add-source" in str(ei.value).lower()


def test_empty_string_raises():
    with pytest.raises(sourceurl.SourceDetectionError):
        d("")


def test_label_derivation_hyphens_to_spaces():
    assert d("https://boards.greenhouse.io/some-co").label == "Some Co"


def test_explicit_label_overrides():
    assert d("https://boards.greenhouse.io/stripe", label="My Stripe").label == "My Stripe"


def test_github_label_from_user_and_repo():
    e = d("https://github.com/octo/cool-list/blob/main/README.md")
    assert e.label == "Octo Cool List"


import pytest as _pytest  # noqa: E402


@_pytest.mark.parametrize("url", [
    "https://boards.greenhouse.io",
    "https://jobs.lever.co",
    "https://jobs.ashbyhq.com",
    "https://raw.githubusercontent.com/x/y",          # < 4 segments
    "https://github.com/u/r/blob/main",                # blob present but no file path
])
def test_slugless_or_incomplete_urls_raise(url):
    with _pytest.raises(sourceurl.SourceDetectionError):
        d(url)


def test_markdown_extension_blob_resolves_to_markdown():
    e = d("https://github.com/u/r/blob/main/list.markdown")
    assert e.type == "markdown"
