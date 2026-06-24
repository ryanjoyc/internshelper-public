"""Greenhouse public job board API.

GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
returns every job (with description content) in a single response — no pagination.
"""

from __future__ import annotations

from internshelper.clock import to_iso
from internshelper.connectors.base import Connector, register
from internshelper.models import Posting


@register
class GreenhouseConnector(Connector):
    type = "greenhouse"

    def url(self) -> str:
        return f"https://boards-api.greenhouse.io/v1/boards/{self.entry.token}/jobs"

    def fetch(self) -> list[Posting]:
        return self.parse(self._get(self.url(), params={"content": "true"}))

    def parse(self, data) -> list[Posting]:
        postings = []
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        for job in jobs:
            # Location is normally {"name": ...}; tolerate a bare string (or missing) without
            # crashing the whole board.
            loc = job.get("location")
            location = loc.get("name", "") or "" if isinstance(loc, dict) else (str(loc) if loc else "")
            postings.append(
                Posting(
                    posting_id=f"{self.type}:{job['id']}",
                    source_key=self.source_key,
                    title=(job.get("title") or "").strip(),
                    company=job.get("company_name") or self.company_fallback,
                    url=job.get("absolute_url", ""),
                    location=location,
                    description=job.get("content", "") or "",
                    posted_at=to_iso(job.get("first_published") or job.get("updated_at")),
                    raw=job,
                )
            )
        return postings
