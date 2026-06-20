import hashlib
from pathlib import Path

from internshelper.config import SourceEntry
from internshelper.connectors import build_connector
from internshelper.connectors import markdown_list

FX = Path(__file__).parent / "fixtures"
SNDSH = "https://raw.githubusercontent.com/sndsh404/summer-2027-internships/main/README.md"
VANSH = "https://raw.githubusercontent.com/vanshb03/Summer2026-Internships/dev/README.md"


def _conn(url):
    return build_connector(SourceEntry(type="markdown", token=url, label="L"))


def test_sndsh404_parses_apply_markdown_links():
    posts = _conn(SNDSH).parse((FX / "md_sndsh404.md").read_text())
    assert len(posts) == 8  # all 8 data rows
    p = posts[0]
    assert p.company == "Circleback"
    assert "Software Engineering Intern" in p.title
    assert p.location == "San Francisco, CA"
    assert p.url == ("https://www.ycombinator.com/companies/circleback/jobs/"
                     "QMpriul-software-engineering-intern-summer-2027")
    assert p.posting_id.startswith("markdown:") and len(p.posting_id.split(":", 1)[1]) == 16
    assert p.raw and p.raw["company"] == "Circleback"


def test_vanshb03_href_links_continuation_and_closed_skip():
    posts = _conn(VANSH).parse((FX / "md_vanshb03.md").read_text())
    assert len(posts) == 6  # 7 rows, the 🔒 Granite row skipped
    assert posts[0].company == "Voloridge Investment Management"
    assert posts[0].url.startswith("https://voloridge-investment-management.hiringthing.com/")
    kudu = [p for p in posts if p.company == "Kudu Dynamics"]
    assert len(kudu) == 2  # the Kudu row + the ↳ continuation row inherit the same company
    assert all(p.company != "Granite Construction" for p in posts)  # closed row dropped


def test_posting_id_uses_canonical_url_and_is_stable():
    text = (FX / "md_vanshb03.md").read_text()
    c = _conn(VANSH)
    a = c.parse(text)
    b = c.parse(text)
    ids = [p.posting_id for p in a]
    assert ids == [p.posting_id for p in b]  # stable across parses
    assert len(ids) == len(set(ids))  # unique
    expected = "markdown:" + hashlib.sha1(
        markdown_list.canonical_url(a[0].url).encode()
    ).hexdigest()[:16]
    assert a[0].posting_id == expected


def test_canonical_url_drops_query_fragment_and_trailing_slash():
    a = markdown_list.canonical_url("https://Example.com/Job/123?utm=x&jr_id=9#frag")
    b = markdown_list.canonical_url("https://example.com/Job/123/")
    assert a == b == "https://example.com/Job/123"


def test_malformed_rows_skipped_without_raising():
    md = (
        "| Company | Role | Location | Apply |\n"
        "| --- | --- | --- | --- |\n"
        "| Good Co | Engineer Intern | NYC | [apply](https://good.co/jobs/1) |\n"
        "| broken row no pipes\n"
        "|  |  |  |  |\n"
    )
    posts = _conn(SNDSH).parse(md)
    assert [p.company for p in posts] == ["Good Co"]


def test_parses_all_tables_not_just_the_first():
    md = (
        "## Software\n\n"
        "| Company | Role | Location | Apply |\n"
        "| --- | --- | --- | --- |\n"
        "| A Co | SWE Intern | NYC | [apply](https://a.co/1) |\n\n"
        "## Data\n\n"
        "| Company | Role | Location | Apply |\n"
        "| --- | --- | --- | --- |\n"
        "| B Co | Data Intern | SF | [apply](https://b.co/2) |\n"
    )
    posts = _conn(SNDSH).parse(md)
    assert sorted(p.company for p in posts) == ["A Co", "B Co"]


def test_dedupes_same_posting_across_tables():
    row = "| A Co | SWE Intern | NYC | [apply](https://a.co/1) |"
    md = (
        "| Company | Role | Location | Apply |\n| --- | --- | --- | --- |\n" + row + "\n\n"
        "| Company | Role | Location | Apply |\n| --- | --- | --- | --- |\n" + row + "\n"
    )
    assert len(_conn(SNDSH).parse(md)) == 1


def test_markdown_extracts_posted_at_from_date_column():
    from datetime import datetime, timezone
    now = datetime(2026, 6, 18, tzinfo=timezone.utc)
    s = _conn(SNDSH).parse((FX / "md_sndsh404.md").read_text(), now=now)
    assert s[0].posted_at == "2026-06-16"  # the "Added" column (YYYY-MM-DD)
    v = _conn(VANSH).parse((FX / "md_vanshb03.md").read_text(), now=now)
    assert v[0].posted_at == "2026-05-22"  # "Date Posted: May 22" -> inferred year


def test_build_connector_resolves_markdown():
    assert build_connector(SourceEntry(type="markdown", token="https://x/r.md")).type == "markdown"
