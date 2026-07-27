"""Fetch a posting's job description from its ATS when the source didn't carry one.

Markdown / github-list sources (e.g. speedyapply) give us a title + an apply URL but no
JD. The term classifier needs the description's text to read a season/year, so this module
resolves an apply URL to the ATS's **server-side JSON API** and pulls the description — the
same endpoints the manual run proved (plain page HTML is usually client-rendered and empty).

Design:
- `resolve_api(url)` is **pure**: it maps an apply URL to an `ApiRequest` (the JSON endpoint
  + how to extract description text), or None if the ATS isn't supported. Unit-testable with
  no network.
- `description_for(url, get_json=...)` does the fetch and returns plain text, or "" when the
  ATS is unsupported / the call fails / the body is JS-only. An empty string is an honest
  "couldn't read it" (→ the classifier grades it UNREADABLE), never an exception that aborts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import httpx

from internshelper.connectors.base import HEADERS, TIMEOUT
from internshelper.text import strip_html

JsonGetter = Callable[[str], Any]


@dataclass
class ApiRequest:
    ats: str                              # which ATS matched (diagnostic)
    url: str                              # the JSON endpoint to GET
    extract: Callable[[Any], str]         # payload(JSON) -> raw description text (may be HTML)


def _txt(*parts: Any) -> str:
    """Join non-empty string-ish parts, then strip HTML/whitespace."""
    joined = "\n".join(str(p) for p in parts if p)
    return strip_html(joined).strip()


# --- per-ATS resolvers: (url, host, path, query) -> ApiRequest | None -----------------

def _greenhouse(host: str, path: str, qs: dict[str, list[str]]) -> ApiRequest | None:
    # job-boards.greenhouse.io/{board}/jobs/{id}  OR  boards.greenhouse.io/{board}/jobs/{id}
    m = re.search(r"greenhouse\.io/(?:embed/job_app\?|)?([^/]+)/jobs/(\d+)", f"{host}{path}")
    board = jid = None
    if m:
        board, jid = m.group(1), m.group(2)
    # Any site embedding Greenhouse via ?gh_jid=ID (e.g. sentinelone.com, about.fandom.com).
    if jid is None and qs.get("gh_jid"):
        jid = qs["gh_jid"][0]
        # board unknown from URL: guess from the registrable host label (sentinelone, fandom...).
        board = host.split(".")[-2] if host.count(".") >= 1 else host
    if not jid or not board:
        return None
    api = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{jid}?content=true"
    return ApiRequest("greenhouse", api, lambda d: _txt(d.get("content")))


def _lever(host: str, path: str, qs: dict[str, list[str]]) -> ApiRequest | None:
    if "lever.co" not in host:
        return None
    m = re.search(r"^/([^/]+)/([0-9a-f-]{36})", path)
    if not m:
        return None
    org, jid = m.group(1), m.group(2)
    api = f"https://api.lever.co/v0/postings/{org}/{jid}?mode=json"

    def extract(d: Any) -> str:
        lists = " ".join(
            f"{it.get('text', '')} {it.get('content', '')}" for it in (d.get("lists") or [])
        )
        return _txt(d.get("description"), lists, d.get("additional"))

    return ApiRequest("lever", api, extract)


def _ashby(host: str, path: str, qs: dict[str, list[str]]) -> ApiRequest | None:
    if "ashbyhq.com" not in host:
        return None
    m = re.search(r"^/([^/]+)/([0-9a-f-]{36})", path)
    if not m:
        return None
    org, jid = m.group(1), m.group(2)
    api = f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true"

    def extract(d: Any) -> str:
        for job in (d.get("jobs") or []):
            if job.get("id") == jid:
                return _txt(job.get("descriptionPlain") or job.get("descriptionHtml"))
        return ""

    return ApiRequest("ashby", api, extract)


def _smartrecruiters(host: str, path: str, qs: dict[str, list[str]]) -> ApiRequest | None:
    if "smartrecruiters.com" not in host:
        return None
    m = re.search(r"^/([^/]+)/(\d+)", path)
    if not m:
        return None
    co, jid = m.group(1), m.group(2)
    api = f"https://api.smartrecruiters.com/v1/companies/{co}/postings/{jid}"

    def extract(d: Any) -> str:
        ad = (d.get("jobAd") or {}).get("sections") or {}
        parts = []
        for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"):
            sec = ad.get(key) or {}
            if sec.get("text"):
                parts.append(sec["text"])
        return _txt(*parts)

    return ApiRequest("smartrecruiters", api, extract)


def _workday(host: str, path: str, qs: dict[str, list[str]]) -> ApiRequest | None:
    # Tenant Workday:  {tenant}.{wdN}.myworkdayjobs.com/{locale}/{site}/job/{rest...}
    # Shared Workday:  {wdN}.myworkdaysite.com/recruiting/{tenant}/{site}/job/{rest...}
    if "myworkdayjobs.com" in host:
        tenant = host.split(".")[0]
        m = re.search(r"^/[^/]+/([^/]+)/job/(.+)$", path)  # /en-US/{site}/job/{rest}
        if not m:
            return None
        site, rest = m.group(1), m.group(2)
    elif "myworkdaysite.com" in host:
        m = re.search(r"^/(?:[^/]+/)?recruiting/([^/]+)/([^/]+)/job/(.+)$", path)
        if not m:
            return None
        tenant, site, rest = m.group(1), m.group(2), m.group(3)
    else:
        return None
    api = f"https://{host}/wday/cxs/{tenant}/{site}/job/{rest}"

    def extract(d: Any) -> str:
        info = d.get("jobPostingInfo") or {}
        return _txt(info.get("jobDescription"))

    return ApiRequest("workday", api, extract)


_RESOLVERS = (_greenhouse, _lever, _ashby, _smartrecruiters, _workday)


def resolve_api(url: str) -> ApiRequest | None:
    """Map an apply URL to its ATS JSON endpoint + extractor, or None if unsupported.

    Pure (no network). iCIMS / Workable-JS / bespoke career sites return None — the caller
    treats that as "couldn't read it" (UNREADABLE), which is honest, not a failure.
    """
    if not url:
        return None
    s = urlsplit(url)
    host, path, qs = s.netloc.lower(), s.path, parse_qs(s.query)
    for resolver in _RESOLVERS:
        req = resolver(host, path, qs)
        if req is not None:
            return req
    return None


def _default_get_json(api_url: str) -> Any:
    resp = httpx.get(api_url, timeout=TIMEOUT, headers={**HEADERS, "Accept": "application/json"},
                     follow_redirects=True)
    resp.raise_for_status()
    return resp.json()


def description_for(url: str, get_json: JsonGetter = _default_get_json) -> str:
    """Best-effort plain-text job description for an apply URL.

    Returns "" when the ATS is unsupported, the request fails, or the body is empty — an
    honest "no readable description". `get_json` is injectable for offline tests.
    """
    req = resolve_api(url)
    if req is None:
        return ""
    try:
        payload = get_json(req.url)
    except Exception:
        return ""
    try:
        return req.extract(payload) or ""
    except Exception:
        return ""
