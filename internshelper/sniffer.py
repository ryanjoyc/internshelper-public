"""Heuristic, no-AI detection of embedded ATS boards on an arbitrary careers page.

`sourceurl.detect_source` resolves a *clean* board URL by host+path alone. When that fails —
a company's own careers page that merely *embeds* a board — this module fetches the page HTML
and regex-scans for an embedded Greenhouse / Lever / Ashby board (iframe/script `src`, anchor
hrefs, inline API endpoints), resolving each to a `SourceEntry`.

Regex-only: no HTML-parser dependency, and deliberately conservative (skip anything ambiguous)
to honor the project's accuracy contract — a wrong source is worse than no source, and the user
can always paste the explicit board URL.
"""

from __future__ import annotations

import re

import httpx

from internshelper.config import SourceEntry
from internshelper.connectors.base import HEADERS, TIMEOUT


class SnifferError(RuntimeError):
    """The careers page couldn't be fetched (network / HTTP error)."""


# Tokens that are URL scaffolding, never a real board slug — filtered out (compared lowercased).
_STOPLIST = {"embed", "assets", "static", "v0", "v1", "job_board", "jobs", "api", "www", "js"}

# (type, pattern, lowercase_token). Capture group 1 is the board slug / org.
# Ashby orgs are case-sensitive, so its tokens are NOT lowercased.
_PATTERNS: list[tuple[str, re.Pattern[str], bool]] = [
    ("greenhouse",
     re.compile(r"boards\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)", re.I), True),
    ("greenhouse", re.compile(r"job-boards\.greenhouse\.io/([A-Za-z0-9_-]+)", re.I), True),
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)", re.I), True),
    ("lever", re.compile(r"jobs\.lever\.co/([A-Za-z0-9_-]+)", re.I), True),
    ("lever", re.compile(r"api\.lever\.co/v0/postings/([A-Za-z0-9_-]+)", re.I), True),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)", re.I), False),
    ("ashby", re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_-]+)", re.I), False),
]


def _label(text: str) -> str:
    return text.replace("-", " ").replace("_", " ").title()


def find_boards_in_html(html: str, *, label: str | None = None) -> list[SourceEntry]:
    """Pure (no network) regex scan of a page's HTML for embedded ATS boards.

    Returns deduped `SourceEntry` candidates, in first-seen order.
    """
    found: dict[str, SourceEntry] = {}
    for stype, pat, lower in _PATTERNS:
        for m in pat.finditer(html):
            token = m.group(1)
            if token.lower() in _STOPLIST:
                continue
            if lower:
                token = token.lower()
            entry = SourceEntry(type=stype, token=token, label=label or _label(token))
            found.setdefault(entry.source_key, entry)
    return list(found.values())


def sniff_careers_page(url: str, *, label: str | None = None) -> list[SourceEntry]:
    """Fetch a careers-page URL and return embedded-board candidates (possibly empty).

    Raises `SnifferError` if the page can't be fetched, so callers can distinguish
    "couldn't load the page" from "loaded, found nothing".
    """
    if "://" not in url:
        url = "https://" + url
    try:
        resp = httpx.get(url, timeout=TIMEOUT, headers=HEADERS, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise SnifferError(f"couldn't fetch {url!r}: {type(e).__name__}: {e}") from e
    return find_boards_in_html(resp.text, label=label)
