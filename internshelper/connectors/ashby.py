"""Ashby public job board API.

GET https://api.ashbyhq.com/posting-api/job-board/{org} returns all jobs in one
response. Only `isListed` jobs are publicly posted, so unlisted ones are skipped
(keeping close-detection honest). Ashby has no company field — the board is the company.
"""

from __future__ import annotations

from internshelper.clock import to_iso
from internshelper.connectors.base import Connector, register
from internshelper.models import Posting


@register
class AshbyConnector(Connector):
    type = "ashby"

    def url(self) -> str:
        return f"https://api.ashbyhq.com/posting-api/job-board/{self.entry.token}"

    def fetch(self) -> list[Posting]:
        return self.parse(self._get(self.url()))

    def parse(self, data) -> list[Posting]:
        postings = []
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        for job in jobs:
            # Safe-by-default: only an explicit isListed=true is treated as posted. A missing
            # key means exclude — never leak an unlisted/closed role if the flag ever vanishes.
            if not job.get("isListed", False):
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
