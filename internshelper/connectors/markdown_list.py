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
from internshelper.connectors.base import HEADERS, TIMEOUT, Connector, register
from internshelper.models import Posting
from internshelper.text import strip_html

# Column header aliases (case-insensitive). A per-source `columns` override wins.
_HEADER_ALIASES = {
    "company": ["company", "employer"],
    "title": ["role", "position", "title", "job"],
    "location": ["location", "locations", "loc"],
    "url": ["application/link", "application", "apply", "apply link", "link"],
    "posted": ["added", "date posted", "date", "posted"],
}
_CONTINUATION_MARKS = {"↳", "⤷", "->", "<-"}
_CLOSED_MARKERS = ["🔒"]


def canonical_url(url: str) -> str:
    """Lowercase scheme+host + path, dropping query/fragment + trailing slash (stable id key)."""
    if not url:
        return ""
    s = urlsplit(url)
    return urlunsplit((s.scheme.lower(), s.netloc.lower(), s.path.rstrip("/"), "", ""))


def _synth_id(url: str, company: str, title: str, location: str) -> str:
    # Include the title alongside the canonical URL: two distinct roles whose apply links
    # differ only by a query param (e.g. ?gh_jid=) share a canonical URL, and would otherwise
    # collapse onto one id. The title keeps them distinct; identical url+title still dedupes.
    if url:
        key = f"{canonical_url(url)}|{title.strip().lower()}"
    else:
        key = f"{company}|{title}|{location}".lower()
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


def _cell(cells: list[str], i: int | None) -> str:
    """A cell by index, or '' if the column is unmapped or the row is shorter than the index.

    Lets an optional column (location/url/posted) be missing on a given row without skipping
    the whole row — required columns are guarded separately by the `need` floor.
    """
    return cells[i] if (i is not None and i < len(cells)) else ""


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
    m = re.search(r"https?://[^\s)<>\]]+", cell)  # bare URL; stop at ) < > ] (autolink/markup)
    if m:
        return m.group(0).rstrip(".,;:")
    return ""


def _header_tokens(header: str) -> set[str]:
    """The header split into lowercase alphanumeric tokens (for whole-token alias matching)."""
    return {t for t in re.split(r"[^a-z0-9]+", header.lower()) if t}


def _map_columns(header_cells: list[str], override: dict[str, str]) -> dict[str, int]:
    lower = [h.lower() for h in header_cells]
    tokens = [_header_tokens(h) for h in header_cells]
    idx: dict[str, int] = {}
    for field, aliases in _HEADER_ALIASES.items():
        name = override.get(field, "").lower()
        if name and name in lower:
            idx[field] = lower.index(name)
            continue
        hit = next((lower.index(a) for a in aliases if a in lower), None)  # exact header first
        if hit is None:
            # Whole-token match — an alias must be a full token of the header (so "loc" matches
            # "Loc"/"Job Loc" but NOT "Allocation"). Kills the greedy-substring mismap.
            hit = next((j for j, toks in enumerate(tokens)
                        if any(a in toks for a in aliases)), None)
        if hit is not None:
            idx[field] = hit
    return idx


@register
class MarkdownListConnector(Connector):
    type = "markdown"

    def fetch(self) -> list[Posting]:
        resp = httpx.get(self.entry.token, timeout=TIMEOUT, headers=HEADERS, follow_redirects=True)
        resp.raise_for_status()
        return self.parse(resp.text, now=datetime.now(timezone.utc))

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
        idx = _map_columns(_cells(lines[header_i]), self.entry.columns)
        if "company" not in idx or "title" not in idx:
            missing = [f for f in ("company", "title") if f not in idx]
            self._warn(
                f"table at line {header_i + 1}: couldn't map required column(s) "
                f"{', '.join(missing)} — headers were {_cells(lines[header_i])}. "
                f"Map them with --columns {missing[0]}=<header>"
                + (f",{missing[1]}=<header>" if len(missing) > 1 else "")
            )
            return []  # can't make a posting without these
        # Floor on required columns only. A row just needs enough cells to carry company+title;
        # missing trailing *optional* columns (location/url/posted) must not skip the row.
        need = max(idx["company"], idx["title"])

        out: list[Posting] = []
        last_company = ""
        data_rows = 0
        k = header_i + 2
        while k < len(lines):
            line = lines[k]
            if not line.strip().startswith("|"):
                break  # table ended
            # A row immediately followed by a separator is the next table's header — stop here.
            if k + 1 < len(lines) and _is_separator(lines[k + 1]):
                break
            k += 1
            if _is_separator(line):
                continue
            cells = _cells(line)
            if len(cells) <= need:
                continue  # too few columns to carry even company + title

            closed = any(m in line for m in _CLOSED_MARKERS)
            company_cell = _clean(cells[idx["company"]])
            for m in _CLOSED_MARKERS:
                company_cell = company_cell.replace(m, "")
            company_cell = company_cell.strip()
            if not company_cell or company_cell in _CONTINUATION_MARKS:
                company = last_company
            else:
                company = company_cell
                last_company = company  # precedent carries forward, even from a closed row
            if closed:
                continue  # filled/closed role: not emitted, but its company is now the precedent

            title = _clean(cells[idx["title"]])
            if not title:
                continue
            location = _clean(_cell(cells, idx.get("location")))
            url = _extract_url(_cell(cells, idx.get("url")))
            posted_cell = _clean(_cell(cells, idx.get("posted")))
            posted_at = to_iso(posted_cell, now=now) if posted_cell else None

            data_rows += 1
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
        if data_rows == 0:
            self._warn(f"table at line {header_i + 1}: header mapped but no data rows parsed")
        return out

    @staticmethod
    def _find_headers(lines: list[str]) -> list[int]:
        return [
            i for i in range(len(lines) - 1)
            if lines[i].strip().startswith("|") and _is_separator(lines[i + 1])
        ]
