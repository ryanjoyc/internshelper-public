"""Ashby public job board API.

GET https://api.ashbyhq.com/posting-api/job-board/{org} returns all jobs in one
response. Only `isListed` jobs are publicly posted, so unlisted ones are skipped
(keeping close-detection honest). Ashby has no company field — the board is the company.
"""

from __future__ import annotations

from internshelper.clock import to_iso
from internshelper.connectors.base import Connector, FetchResult, register
from internshelper.models import Posting


@register
class AshbyConnector(Connector):
    type = "ashby"

    def url(self) -> str:
        return f"https://api.ashbyhq.com/posting-api/job-board/{self.entry.token}"

    def fetch(self) -> FetchResult:
        postings = self.parse(self._get(self.url()))
        return FetchResult(tuple(postings), complete=True)

    def parse(self, data) -> list[Posting]:
        postings = []
        for job in data.get("jobs", []):
            if not job.get("isListed", True):
                continue
            postings.append(
                Posting(
                    posting_id=f"{self.type}:{job['id']}",
                    source_key=self.source_key,
                    title=(job.get("title") or "").strip(),
                    company=self.company_fallback,
                    url=job.get("jobUrl") or job.get("applyUrl", ""),
                    location=job.get("location", "") or "",
                    description=job.get("descriptionPlain", "") or "",
                    posted_at=to_iso(job.get("publishedAt")),
                    raw=job,
                )
            )
        return postings
