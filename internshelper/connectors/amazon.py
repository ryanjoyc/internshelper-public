"""Amazon.jobs public search API — Amazon's own ATS (not Greenhouse/Lever/Ashby/Workday).

GET https://www.amazon.jobs/en/search.json?base_query=<q>&normalized_country_code[]=USA
    &result_limit=<n>&offset=<n>&sort=recent  — paginate by offset until `hits` are exhausted.

Amazon has no enumerable board: you SEARCH. The source token IS the query (e.g. "intern");
scope is US-only (matches the source we ship). Quirks baked in:

- Like Workday, incomplete pagination (empty-page-before-`hits`, the MAX_POSTINGS cap, or 4xx)
  raises. Missing/contradictory counts, overlapping pages, or skipped malformed rows return valid
  postings with `complete=False`, so they cannot advance absence or close detection.
- `hits` is read only from the first page (defensive — same discipline as Workday's `total`).
- `posted_date` is a human string with a padded day ("July  7, 2026"); we collapse the
  whitespace and let `clock.to_iso` parse it (falls back to None if unparseable).
- The keyword search returns the occasional non-intern role (e.g. a recruiter role that mentions
  "internship"); the source's `title_must_match` guard filters those out, same as every board.
"""

from __future__ import annotations

import re

import httpx

from internshelper import clock
from internshelper.connectors.base import Connector, FetchResult, register
from internshelper.models import Posting

SEARCH_URL = "https://www.amazon.jobs/en/search.json"
PAGE_LIMIT = 100  # amazon.jobs accepts up to 100 results per page
COUNTRY = "USA"
# Refuse to enumerate a query matching more than this — a collector, not a crawler. The
# raise-message points at narrowing the query (the source token), the intended fix.
MAX_POSTINGS = 3000


@register
class AmazonConnector(Connector):
    type = "amazon"

    def fetch(self) -> FetchResult:
        query = self.entry.token
        pages: list[dict] = []
        got, total = 0, None
        while True:
            try:
                page = self._get(
                    SEARCH_URL,
                    params={
                        "base_query": query,
                        "normalized_country_code[]": COUNTRY,
                        "result_limit": PAGE_LIMIT,
                        "offset": got,
                        "sort": "recent",
                    },
                )
            except httpx.HTTPStatusError as e:
                if e.response is not None and 400 <= e.response.status_code < 500:
                    raise RuntimeError(
                        f"amazon.jobs rejected the search ({e.response.status_code}): "
                        f"query {query!r} may be malformed — source can't be collected"
                    ) from e
                raise
            if not pages:
                total = self._declared_count(page, "hits")
                # Trust the first page's count only.
            if total is not None:
                if total > MAX_POSTINGS:
                    raise RuntimeError(
                        f"amazon.jobs query {query!r} matched {total} postings; refusing a "
                        f"{total // PAGE_LIMIT}-page crawl — narrow the query (the source token)"
                    )
            items = page.get("jobs") or []
            if not items and total is not None and got < total:
                raise RuntimeError(
                    f"amazon.jobs returned an empty page at offset {got} with "
                    f"{total - got} of {total} postings still expected — refusing a partial "
                    "fetch (it would falsely close live postings)"
                )
            pages.append(page)
            got += len(items)
            if total is None or got >= total or not items:
                break
        postings = self.parse(pages)
        # A malformed row is positive data we can safely skip, but it makes the
        # enumeration incomplete for absence/close detection.
        return FetchResult(
            tuple(postings),
            complete=self._pagination_complete(
                postings,
                fetched=got,
                expected=total,
                count_field="hits",
            ),
        )

    def parse(self, pages: list[dict]) -> list[Posting]:
        self.diagnostics = []  # fresh per parse, same contract as the other connectors
        postings: list[Posting] = []
        for page in pages:
            for j in page.get("jobs") or []:
                jid = j.get("id_icims") or j.get("id")
                path = j.get("job_path")
                if not jid or not path:
                    self._warn(f"skipped {j.get('title')!r}: missing id/job_path")
                    continue
                desc = " ".join(
                    x for x in (j.get("description"), j.get("basic_qualifications")) if x
                )
                posted = re.sub(r"\s+", " ", j.get("posted_date") or "").strip() or None
                postings.append(
                    Posting(
                        posting_id=f"{self.type}:{jid}",
                        source_key=self.source_key,
                        title=(j.get("title") or "").strip(),
                        company="Amazon",  # the board IS Amazon; keep subsidiaries in one group
                        url="https://www.amazon.jobs" + path,
                        location=j.get("normalized_location") or j.get("location") or "",
                        description=desc,
                        posted_at=clock.to_iso(posted),
                        raw=j,
                    )
                )
        return postings
