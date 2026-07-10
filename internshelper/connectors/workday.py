"""Workday CXS job-board API (banks, card networks, aerospace — the big-corp ATS).

POST https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs with
{"appliedFacets": {}, "limit": 20, "offset": N, "searchText": ...} and paginate by offset.
Quirks learned from live captures, baked in here:

- `total` is only trustworthy on the FIRST page — later pages really report `total: 0`.
- `postedOn` is relative prose ("Posted 30+ Days Ago") and the detail API's `startDate` is the
  POSTING date, not the internship start — so `posted_at` is None; never guess a date.
- No company field in the payload (the board IS the company) and no description in the list
  payload; fetching per-job details would be N+1 requests on 1000-req tenants, so postings
  carry `description=""` like the markdown/github list connectors.
- **A partial fetch must raise, never return.** `store.apply_close_detection` closes every
  active posting absent from a successful return, so returning a partial page-set would
  mass-close live postings. Empty-page-before-total, the MAX_POSTINGS cap, and 4xx all raise.
- Known v1 limitation: the rare tenant that demands a browser session/CSRF cookie fails as
  unhealthy with a clear message (no cookie-jar fallback); `*.myworkdaysite.com` hosts are
  out of scope.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import httpx

from internshelper.connectors.base import Connector, register
from internshelper.models import Posting

PAGE_LIMIT = 20  # the CXS server cap per request
# Refuse to enumerate boards larger than this: 100 pages/cycle is where "collector" ends and
# "crawler" begins. The raise-message points at the `search:` key, which is the intended fix.
MAX_POSTINGS = 2000

_HOST_RE = re.compile(r"^([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com$")
_LOCALE_RE = re.compile(r"^[a-z]{2}-[a-z]{2}$", re.IGNORECASE)


@dataclass(frozen=True)
class WorkdayBoard:
    tenant: str  # "mastercard"
    host: str  # "mastercard.wd1.myworkdayjobs.com"
    site: str  # "CorporateCareers" (case preserved — CXS paths are case-sensitive)

    @property
    def base_url(self) -> str:
        return f"https://{self.host}/{self.site}"

    @property
    def jobs_api(self) -> str:
        return f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}/jobs"


def parse_board_url(url: str) -> WorkdayBoard:
    """Canonicalize any myworkdayjobs URL (board, locale variant, or single-job link) to its
    board. Raises ValueError for anything that isn't a Workday board URL."""
    m = re.match(r"^https?://([^/?#]+)(/[^?#]*)?", (url or "").strip())
    if not m:
        raise ValueError(f"not a URL: {url!r}")
    host_m = _HOST_RE.match(m.group(1).lower())
    if not host_m:
        raise ValueError(f"not a *.wd(N).myworkdayjobs.com host: {url!r}")
    parts = [p for p in (m.group(2) or "").split("/") if p]
    if parts and _LOCALE_RE.match(parts[0]):
        parts = parts[1:]  # drop the locale segment (/en-US/, /fr-CA/, …)
    if not parts:
        raise ValueError(f"no board site in URL (expected …myworkdayjobs.com/<site>): {url!r}")
    return WorkdayBoard(tenant=host_m.group(1), host=host_m.group(0), site=parts[0])


@register
class WorkdayConnector(Connector):
    type = "workday"

    def fetch(self) -> list[Posting]:
        board = parse_board_url(self.entry.token)
        search = self.entry.search
        pages: list[dict] = []
        got, total = 0, None
        while True:
            try:
                page = self._post_json(
                    board.jobs_api,
                    {"appliedFacets": {}, "limit": PAGE_LIMIT, "offset": got,
                     "searchText": search},
                )
            except httpx.HTTPStatusError as e:
                if e.response is not None and 400 <= e.response.status_code < 500:
                    raise RuntimeError(
                        f"workday CXS rejected {board.jobs_api} "
                        f"({e.response.status_code}): the site name may be wrong, or this "
                        "tenant requires a browser session — source can't be collected"
                    ) from e
                raise
            if total is None:
                total = int(page.get("total") or 0)  # ONLY the first page reports total
                if total > MAX_POSTINGS:
                    raise RuntimeError(
                        f"workday board {board.base_url} has {total} postings; refusing a "
                        f"{total // PAGE_LIMIT}-page crawl — add `search:` to the source "
                        "(e.g. search: intern) to narrow it server-side"
                    )
            items = page.get("jobPostings") or []
            if not items and got < total:
                raise RuntimeError(
                    f"workday board {board.base_url} returned an empty page at offset {got} "
                    f"with {total - got} of {total} postings still expected — refusing a "
                    "partial fetch (it would falsely close live postings)"
                )
            pages.append(page)
            got += len(items)
            if got >= total or not items:
                break
        postings = self.parse(pages)
        if search:
            self._warn(
                f"server-side search {search!r} active — postings not matching it are "
                "invisible to this source (close-detection applies within the search)"
            )
        return postings

    def parse(self, pages: list[dict]) -> list[Posting]:
        self.diagnostics = []  # fresh per parse, same contract as the markdown connector
        board = parse_board_url(self.entry.token)
        postings: list[Posting] = []
        for page in pages:
            for item in page.get("jobPostings") or []:
                path = item.get("externalPath")
                if not path:
                    self._warn(
                        f"skipped {item.get('title')!r}: no externalPath (can't build a "
                        "stable id or URL)"
                    )
                    continue
                sid = hashlib.sha1(f"{board.site}{path}".encode()).hexdigest()[:16]
                postings.append(
                    Posting(
                        posting_id=f"{self.type}:{board.tenant}:{sid}",
                        source_key=self.source_key,
                        title=(item.get("title") or "").strip(),
                        company=self.company_fallback,
                        url=board.base_url + path,
                        location=item.get("locationsText") or "",
                        description="",  # list payload carries no JD; no N+1 detail fetches
                        posted_at=None,  # postedOn is relative prose; startDate = posting date
                        raw=item,
                    )
                )
        return postings
