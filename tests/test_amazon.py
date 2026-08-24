"""Amazon.jobs connector: parse (frozen fixture, no network) + URL detection."""

from internshelper.config import SourceEntry
from internshelper.connectors.amazon import AmazonConnector
from internshelper.sourceurl import detect_source

_PAGE = {
    "hits": 3,
    "jobs": [
        {
            "id_icims": "10468083",
            "title": "Software Development Engineer Intern - AWS ",
            "job_path": "/en/jobs/10468083/software-development-engineer-intern-aws",
            "normalized_location": "Seattle, WA, USA",
            "city": "Seattle", "state": "WA", "country_code": "USA",
            "posted_date": "July  7, 2026",  # padded day, as Amazon really sends it
            "description": "Build backend systems.",
            "basic_qualifications": "Pursuing a CS degree.",
            "company_name": "Amazon.com Services LLC",
        },
        {
            "id_icims": "10468069",
            "title": "Public Policy Intern",
            "job_path": "/en/jobs/10468069/public-policy-intern",
            "location": "Arlington, VA, USA",
            "posted_date": "",  # unparseable -> posted_at None
        },
        {  # missing id AND job_path -> skipped with a diagnostic
            "title": "Broken row", "job_path": None,
        },
    ],
}


def _conn(token="intern"):
    return AmazonConnector(SourceEntry(type="amazon", token=token))


def test_parse_builds_postings_with_stable_id_and_url():
    posts = _conn().parse([_PAGE])
    assert len(posts) == 2  # the broken row is dropped
    p = posts[0]
    assert p.posting_id == "amazon:10468083"
    assert p.source_key == "amazon:intern"
    assert p.url == "https://www.amazon.jobs/en/jobs/10468083/software-development-engineer-intern-aws"
    assert p.company == "Amazon"  # subsidiaries collapse to one group
    assert p.location == "Seattle, WA, USA"
    assert "Build backend systems." in p.description and "CS degree" in p.description
    assert p.posted_at.startswith("2026-07-07")  # "July  7, 2026" -> ISO


def test_parse_handles_missing_date_and_records_skip_diagnostic():
    conn = _conn()
    posts = conn.parse([_PAGE])
    assert posts[1].posted_at is None
    assert conn.diagnostics and "Broken row" in conn.diagnostics[0]


def test_detect_source_maps_amazon_search_to_query_token():
    e = detect_source("https://www.amazon.jobs/en/search?base_query=intern")
    assert e.type == "amazon" and e.token == "intern"
    assert e.source_key == "amazon:intern" and e.label == "Amazon"


def test_detect_source_defaults_query_and_decodes():
    assert detect_source("https://www.amazon.jobs").token == "intern"  # bare host -> default
    assert detect_source("amazon.jobs/en/search?base_query=data%20science").token == "data science"
