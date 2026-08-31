"""Connector for curated internship lists published as a README Markdown table.

Most community lists (e.g. sndsh404/summer-2027-internships, vanshb03/Summer2026-Internships)
are hand-maintained Markdown tables, not the structured JSON the `github` connector reads.
This parses the table generically (auto-detected columns), handles `<a href>` and `[apply]()`
link styles, `↳` continuation rows, and `🔒` closed markers, and keys each row on a stable
synthetic id (sha1 of the canonicalized apply URL).
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx

from internshelper.clock import to_iso
from internshelper.connectors.base import HEADERS, TIMEOUT, Connector, FetchResult, register
from internshelper.models import Posting
from internshelper.text import strip_html

# Column header aliases (case-insensitive). A per-source `columns` override wins.
_HEADER_ALIASES = {
    # "org"/"program" name the entity; "opportunity"/"focus" describe the offering. These cover
    # the sndsh404 "programs" sections, whose apply link sits inside the entity/offering cell
    # rather than a dedicated Apply column (recovered by the whole-row URL fallback below).
    "company": ["company", "employer", "org", "program"],
    "title": ["role", "position", "title", "job", "opportunity", "focus"],
    "location": ["location", "locations", "loc"],
    "url": ["application/link", "application", "apply", "apply link", "link", "posting"],
    "posted": ["added", "date posted", "date", "posted"],
}
_CONTINUATION_MARKS = {"↳", "⤷", "->", "<-"}
_CLOSED_MARKERS = ["🔒"]
# Sentinel for the per-source columns override: `columns: company=@heading` means the list has
# no company column — the firm is the nearest Markdown heading above each table (the NUFT
# quant-list shape: `## Citadel Securities` followed by a `|Role|Links|` table). Opt-in only,
# so ordinary lists keep the loud can't-map-company diagnostic instead of guessing.
COMPANY_FROM_HEADING = "@heading"
_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)")


def _heading_company(lines: list[str], header_i: int) -> str:
    """Nearest Markdown heading above the table — the firm name in per-section lists."""
    for j in range(header_i - 1, -1, -1):
        m = _HEADING_RE.match(lines[j].strip())
        if m:
            return _clean(m.group(1))
    return ""


def canonical_url(url: str) -> str:
    """Lowercase scheme+host + path, dropping query/fragment + trailing slash (stable id key)."""
    if not url:
        return ""
    s = urlsplit(url)
    return urlunsplit((s.scheme.lower(), s.netloc.lower(), s.path.rstrip("/"), "", ""))


def _synth_id(url: str, company: str, title: str, location: str) -> str:
    key = canonical_url(url) if url else f"{company}|{title}|{location}".lower()
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def _cells(line: str) -> list[str]:
    parts = [c.strip() for c in line.split("|")]
    if parts and parts[0] == "":
        parts = parts[1:]
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts


def _is_separator(line: str) -> bool:
    s = line.strip()
    return bool(s) and set(s) <= set("|:- \t") and "-" in s


def _clean(cell: str) -> str:
    cell = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cell)  # [txt](url) -> txt
    return strip_html(cell).strip()  # drop <a>/<img>/tags, collapse whitespace


def _extract_url(cell: str) -> str:
    m = re.search(r"""<a[^>]+href=["']([^"']+)["']""", cell)
    if m:
        return m.group(1)
    m = re.search(r"\[[^\]]*\]\((https?://[^)\s]+)\)", cell)
    if m:
        return m.group(1)
    m = re.search(r"(https?://[^\s)]+)", cell)
    if m:
        return m.group(1).rstrip(".,")
    return ""


def _map_columns(header_cells: list[str], override: dict[str, str]) -> dict[str, int]:
    lower = [h.lower() for h in header_cells]
    idx: dict[str, int] = {}
    for field, aliases in _HEADER_ALIASES.items():
        name = override.get(field, "").lower()
        if name and name in lower:
            idx[field] = lower.index(name)
            continue
        for a in aliases:  # exact header match first
            if a in lower:
                idx[field] = lower.index(a)
                break
        else:  # substring fallback
            for j, h in enumerate(lower):
                if any(a in h for a in aliases):
                    idx[field] = j
                    break
    return idx


@register
class MarkdownListConnector(Connector):
    type = "markdown"

    def fetch(self) -> FetchResult:
        resp = httpx.get(self.entry.token, timeout=TIMEOUT, headers=HEADERS, follow_redirects=True)
        resp.raise_for_status()
        postings = self.parse(resp.text, now=datetime.now(timezone.utc))
        return FetchResult(tuple(postings), complete=not self.diagnostics)

    def parse(self, text: str, now: datetime | None = None) -> list[Posting]:
        """Parse EVERY table in the document (these lists are split into category sections),
        deduping rows that recur across tables. `now` resolves year-less posted dates."""
        self.diagnostics = []  # fresh per parse; populated when a table can't be mapped
        lines = text.splitlines()
        postings: list[Posting] = []
        seen: set[str] = set()
        headers = self._find_headers(lines)
        if not headers:
            self._warn("no Markdown table found (need a header row followed by a |---| separator)")
        for header_i in headers:
            postings.extend(self._parse_table(lines, header_i, seen, now))
        return postings

    def _parse_table(
        self, lines: list[str], header_i: int, seen: set[str], now: datetime | None
    ) -> list[Posting]:
        heading_mode = self.entry.columns.get("company") == COMPANY_FROM_HEADING
        idx = _map_columns(_cells(lines[header_i]), self.entry.columns)
        if heading_mode:
            idx.pop("company", None)  # company comes from the section heading, never a column
        required = ("title",) if heading_mode else ("company", "title")
        missing = [f for f in required if f not in idx]
        if missing:
            self._warn(
                f"table at line {header_i + 1}: couldn't map required column(s) "
                f"{', '.join(missing)} — headers were {_cells(lines[header_i])}. "
                f"Map them with --columns {missing[0]}=<header>"
                + (f",{missing[1]}=<header>" if len(missing) > 1 else "")
            )
            return []  # can't make a posting without these
        need = max(idx.values())
        table_company = _heading_company(lines, header_i) if heading_mode else ""

        out: list[Posting] = []
        last_company = ""
        k = header_i + 2
        while k < len(lines):
            line = lines[k]
            if not line.strip().startswith("|"):
                break  # table ended
            # A row immediately followed by a separator is the next table's header — stop here.
            if k + 1 < len(lines) and _is_separator(lines[k + 1]):
                break
            k += 1
            if _is_separator(line) or any(m in line for m in _CLOSED_MARKERS):
                continue  # separator row, or a filled/closed role
            cells = _cells(line)
            if len(cells) <= need:
                self._warn(
                    f"table at line {header_i + 1}: skipped malformed row at line {k} "
                    f"(expected at least {need + 1} columns, found {len(cells)})"
                )
                continue

            if heading_mode:
                company = table_company
            else:
                company_cell = _clean(cells[idx["company"]])
                if not company_cell or company_cell in _CONTINUATION_MARKS:
                    company = last_company
                else:
                    company = company_cell
                    last_company = company

            title = _clean(cells[idx["title"]])
            if not title:
                self._warn(
                    f"table at line {header_i + 1}: skipped malformed row at line {k} "
                    "(empty title)"
                )
                continue
            location = _clean(cells[idx["location"]]) if "location" in idx else ""
            url = _extract_url(cells[idx["url"]]) if "url" in idx else ""
            if not url:
                url = _extract_url(line)  # no Apply column (program tables) — link is in-row
            posted_at = (
                to_iso(_clean(cells[idx["posted"]]), now=now) if "posted" in idx else None
            )

            posting_id = f"{self.type}:{_synth_id(url, company, title, location)}"
            if posting_id in seen:
                continue  # same role listed in another category table
            seen.add(posting_id)
            out.append(
                Posting(
                    posting_id=posting_id,
                    source_key=self.source_key,
                    title=title,
                    company=company or self.company_fallback,
                    url=url,
                    location=location,
                    description="",  # markdown rows carry no JD
                    posted_at=posted_at,
                    raw={"company": company, "role": title, "location": location,
                         "url": url, "source_line": line.strip()},
                )
            )
        return out

    @staticmethod
    def _find_headers(lines: list[str]) -> list[int]:
        return [
            i for i in range(len(lines) - 1)
            if lines[i].strip().startswith("|") and _is_separator(lines[i + 1])
        ]
