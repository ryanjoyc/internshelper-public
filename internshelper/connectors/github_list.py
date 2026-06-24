"""Community internship list (structured JSON, e.g. SimplifyJobs listings.json).

The source entry's `token` is the raw listings.json URL. The list includes closed
entries, so only `active` AND `is_visible` rows are ingested — that way close-detection
naturally closes a posting when it drops out of the list. Keyed on the listing's `id`.
"""

from __future__ import annotations

from internshelper.clock import to_iso
from internshelper.connectors.base import Connector, register
from internshelper.models import Posting


def _truthy(value) -> bool:
    """Robust truthiness for JSON flags that may arrive as bools, ints, or strings.

    A string like "false"/"0"/"no" is falsy (some lists serialize booleans as strings);
    bare bools/ints fall through to normal truthiness.
    """
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no")
    return bool(value)


@register
class GithubListConnector(Connector):
    type = "github"

    def fetch(self) -> list[Posting]:
        # token holds the raw listings.json URL.
        return self.parse(self._get(self.entry.token))

    def parse(self, data) -> list[Posting]:
        postings = []
        for item in data if isinstance(data, list) else []:
            if not (_truthy(item.get("active")) and _truthy(item.get("is_visible"))):
                continue
            locs = item.get("locations")
            location = ", ".join(str(x) for x in locs) if isinstance(locs, list) else (str(locs) if locs else "")
            postings.append(
                Posting(
                    posting_id=f"{self.type}:{item['id']}",
                    source_key=self.source_key,
                    title=(item.get("title") or "").strip(),
                    company=item.get("company_name") or self.company_fallback,
                    url=item.get("url", ""),
                    location=location,
                    description="",  # community lists carry no description
                    posted_at=to_iso(item.get("date_posted")),
                    raw=item,
                )
            )
        return postings
