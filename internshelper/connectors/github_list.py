"""Community internship list (structured JSON, e.g. SimplifyJobs listings.json).

The source entry's `token` is the raw listings.json URL. The list includes closed
entries, so only `active` AND `is_visible` rows are ingested — that way close-detection
naturally closes a posting when it drops out of the list. Keyed on the listing's `id`.
"""

from __future__ import annotations

from internshelper.clock import to_iso
from internshelper.connectors.base import Connector, FetchResult, register
from internshelper.models import Posting


@register
class GithubListConnector(Connector):
    type = "github"

    def fetch(self) -> FetchResult:
        # token holds the raw listings.json URL.
        postings = self.parse(self._get(self.entry.token))
        return FetchResult(tuple(postings), complete=True)

    def parse(self, data) -> list[Posting]:
        postings = []
        for item in data:
            if not (item.get("active") and item.get("is_visible")):
                continue
            locations = item.get("locations") or []
            postings.append(
                Posting(
                    posting_id=f"{self.type}:{item['id']}",
                    source_key=self.source_key,
                    title=(item.get("title") or "").strip(),
                    company=item.get("company_name") or self.company_fallback,
                    url=item.get("url", ""),
                    location=", ".join(locations),
                    description="",  # community lists carry no description
                    posted_at=to_iso(item.get("date_posted")),
                    raw=item,
                )
            )
        return postings
