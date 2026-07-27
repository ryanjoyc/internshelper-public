"""ATS apply-URL -> JSON-API resolution + offline description extraction."""

from __future__ import annotations

import pytest

from internshelper import enrich

# (apply_url, expected_ats, fragment_expected_in_api_url)
RESOLVE = [
    ("https://job-boards.greenhouse.io/artefactlinkedin/jobs/7306842002",
     "greenhouse", "boards-api.greenhouse.io/v1/boards/artefactlinkedin/jobs/7306842002"),
    ("https://www.sentinelone.com/jobs/7775825003?gh_jid=7775825003",
     "greenhouse", "boards/sentinelone/jobs/7775825003"),
    ("https://jobs.lever.co/wingtra-2/45a54b09-c92c-4607-b17f-9254de177c16/apply",
     "lever", "api.lever.co/v0/postings/wingtra-2/45a54b09-c92c-4607-b17f-9254de177c16"),
    ("https://jobs.ashbyhq.com/manex/ba2e6b81-606f-4176-9919-2ee0157a8475",
     "ashby", "posting-api/job-board/manex"),
    ("https://jobs.smartrecruiters.com/Grab/744000133290649-intern-software-engineer-?oga=true",
     "smartrecruiters", "companies/Grab/postings/744000133290649"),
    ("https://nvidia.wd5.myworkdayjobs.com/en-US/nvidiaexternalcareersite/job/Switzerland-Zurich/Robotics-Software-Intern_JR2019612",
     "workday", "wday/cxs/nvidia/nvidiaexternalcareersite/job/Switzerland-Zurich/Robotics-Software-Intern_JR2019612"),
    ("https://wd3.myworkdaysite.com/recruiting/magna/Magna/job/Newmarket-Ontario-CA/SWE-Co-op_R00244013",
     "workday", "wday/cxs/magna/Magna/job/Newmarket-Ontario-CA/SWE-Co-op_R00244013"),
]


@pytest.mark.parametrize("url,ats,frag", RESOLVE, ids=[r[1] + str(i) for i, r in enumerate(RESOLVE)])
def test_resolve_api(url, ats, frag):
    req = enrich.resolve_api(url)
    assert req is not None and req.ats == ats
    assert frag in req.url


@pytest.mark.parametrize("url", [
    "https://careers-kinaxis.icims.com/jobs/34832/x/job",       # iCIMS — JS only
    "https://apply.workable.com/reversinglabs/j/C65CFF1E96/",   # workable — not wired
    "https://about.fandom.com/careers/listing/8000909",         # custom site, no gh_jid
    "",                                                          # empty
])
def test_resolve_api_unsupported(url):
    assert enrich.resolve_api(url) is None


def test_description_for_greenhouse_extracts_content():
    url = "https://job-boards.greenhouse.io/acme/jobs/123"
    out = enrich.description_for(url, get_json=lambda _u: {"content": "<p>Summer 2027 intern</p>"})
    assert "Summer 2027 intern" in out


def test_description_for_lever_joins_lists():
    url = "https://jobs.lever.co/acme/45a54b09-c92c-4607-b17f-9254de177c16"
    payload = {"description": "Role desc", "lists": [{"text": "Reqs", "content": "<li>Python</li>"}]}
    out = enrich.description_for(url, get_json=lambda _u: payload)
    assert "Role desc" in out and "Python" in out


def test_description_for_ashby_matches_id():
    jid = "ba2e6b81-606f-4176-9919-2ee0157a8475"
    url = f"https://jobs.ashbyhq.com/acme/{jid}"
    payload = {"jobs": [{"id": jid, "descriptionPlain": "Fall 2026 co-op"},
                        {"id": "other", "descriptionPlain": "nope"}]}
    out = enrich.description_for(url, get_json=lambda _u: payload)
    assert out == "Fall 2026 co-op"


def test_description_for_unsupported_returns_empty():
    assert enrich.description_for("https://careers.icims.com/jobs/1/job", get_json=lambda _u: {}) == ""


def test_description_for_swallows_fetch_errors():
    def boom(_u):
        raise RuntimeError("network down")
    assert enrich.description_for("https://job-boards.greenhouse.io/a/jobs/1", get_json=boom) == ""
