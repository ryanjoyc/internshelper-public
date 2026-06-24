"""Lever public postings API.

GET https://api.lever.co/v0/postings/{token}?mode=json returns a JSON array of all
postings in one response. Lever has no company field — the board is the company.
"""

from __future__ import annotations

from internshelper.clock import to_iso
from internshelper.connectors.base import Connector, register
from internshelper.models import Posting


@register
class LeverConnector(Connector):
    type = "lever"

    def url(self) -> str:
        return f"https://api.lever.co/v0/postings/{self.entry.token}"

    def fetch(self) -> list[Posting]:
        return self.parse(self._get(self.url(), params={"mode": "json"}))

    def parse(self, data) -> list[Posting]:
        postings = []
        for job in data if isinstance(data, list) else []:
            try:
                # `categories` is normally a dict; tolerate a non-dict (and a non-str `text`)
                # rather than letting one malformed posting crash the whole board.
                cats = job.get("categories")
                location = (cats.get("location", "") if isinstance(cats, dict) else "") or ""
                postings.append(
                    Posting(
                        posting_id=f"{self.type}:{job['id']}",
                        source_key=self.source_key,
                        title=str(job.get("text") or "").strip(),
                        company=self.company_fallback,
                        url=job.get("hostedUrl") or job.get("applyUrl", ""),
                        location=location,
                        description=job.get("descriptionPlain", "") or "",
                        posted_at=to_iso(job.get("createdAt")),
                        raw=job,
                    )
                )
            except Exception as e:  # one bad row must not zero the source
                self._warn(f"skipped a malformed Lever posting: {type(e).__name__}: {e}")
        return postings
