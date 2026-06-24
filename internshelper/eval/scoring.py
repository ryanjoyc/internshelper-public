"""Score a connector's parsed output against a hand-labeled golden expectation.

The accuracy metrics this produces are the whole point of the eval harness: role-set
recall/precision (did we capture every job, and only real jobs?) plus per-field
correctness (is each captured field right?). Matching is deliberately
parser-id-independent — a posting is paired to a golden row by its *content* (canonical
URL, or company|title|location when there's no URL), never by the connector's own
`posting_id`. That matters because for the markdown connector the `posting_id` is itself
a synth-hash under test; matching on it would hide the very collisions we're hunting.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from internshelper.connectors.markdown_list import canonical_url
from internshelper.models import Posting

# Fields compared on every matched (golden, posting) pair, each with its own tolerance
# (see `_field_ok`). Order is the scorecard column order.
FIELDS = ["title", "company", "location", "url", "posted_at", "description_present"]


def _norm(s: str | None) -> str:
    return (s or "").strip()


def _as_date(value: str | None) -> str | None:
    """Reduce an ISO-8601 timestamp or date-only string to its UTC calendar date (YYYY-MM-DD).

    `clock.to_iso` already normalized everything to UTC, so the leading 10 chars are the
    UTC date for both `2026-06-02` and `2026-06-02T03:00:00+00:00`.
    """
    if not value:
        return None
    return value[:10]


def _ambiguous_urls(expected: list[dict]) -> set[str]:
    """Canonical URLs that appear on >1 golden row — genuinely distinct roles sharing a URL
    (e.g. two Greenhouse roles whose apply links differ only by `?gh_jid=`). Their match keys
    get disambiguated by title so a parser that collapses them shows up as a recall miss."""
    counts: dict[str, int] = defaultdict(int)
    for g in expected:
        u = canonical_url(g.get("url", ""))
        if u:
            counts[u] += 1
    return {u for u, n in counts.items() if n > 1}


def _key(company: str, title: str, location: str, url: str, ambiguous: set[str]) -> str:
    u = canonical_url(url)
    if u:
        return f"{u}|{_norm(title).lower()}" if u in ambiguous else u
    return f"{_norm(company).lower()}|{_norm(title).lower()}|{_norm(location).lower()}"


def _golden_key(g: dict, ambiguous: set[str]) -> str:
    return _key(g.get("company", ""), g.get("title", ""), g.get("location", ""),
               g.get("url", ""), ambiguous)


def _posting_key(p: Posting, ambiguous: set[str]) -> str:
    return _key(p.company, p.title, p.location, p.url, ambiguous)


def _field_ok(field_name: str, g: dict, p: Posting) -> bool:
    if field_name == "title":
        return _norm(p.title) == _norm(g.get("title"))
    if field_name == "company":
        return _norm(p.company) == _norm(g.get("company"))
    if field_name == "location":
        return _norm(p.location) == _norm(g.get("location"))
    if field_name == "url":
        return canonical_url(p.url) == canonical_url(g.get("url", ""))
    if field_name == "posted_at":
        return _as_date(p.posted_at) == _as_date(g.get("posted_at"))
    if field_name == "description_present":
        from internshelper.text import strip_html
        return bool(strip_html(p.description)) == bool(g.get("description_present"))
    raise ValueError(f"unknown field {field_name!r}")


@dataclass
class FixtureResult:
    """Scored outcome for one fixture: role recall/precision + per-field accuracy + storage."""

    connector: str
    case: str
    expected_n: int
    parsed_n: int
    matched_n: int
    fields: dict[str, tuple[int, int]]  # field -> (correct, total_matched)
    storage: dict
    error: str | None = None
    missed: list[str] = field(default_factory=list)   # golden rows with no parsed match
    extra: list[str] = field(default_factory=list)     # parsed rows matching no golden row
    field_misses: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return 1.0 if self.expected_n == 0 else self.matched_n / self.expected_n

    @property
    def precision(self) -> float:
        if self.parsed_n == 0:
            return 1.0 if self.expected_n == 0 else 0.0
        return self.matched_n / self.parsed_n

    def field_acc(self, name: str) -> float | None:
        correct, total = self.fields.get(name, (0, 0))
        return None if total == 0 else correct / total

    @property
    def fields_perfect(self) -> bool:
        return all(c == t for c, t in self.fields.values())

    @property
    def storage_ok(self) -> bool:
        return bool(self.storage.get("idempotent")) and bool(self.storage.get("no_merge"))

    def is_perfect(self) -> bool:
        return (
            self.error is None
            and self.recall == 1.0
            and self.precision == 1.0
            and self.fields_perfect
            and self.storage_ok
        )

    def diff(self) -> str:
        if self.error is not None:
            return f"{self.connector}/{self.case}: ERROR during parse — {self.error}"
        parts = [f"{self.connector}/{self.case}: recall={self.recall:.0%} "
                 f"precision={self.precision:.0%} "
                 f"(expected {self.expected_n}, parsed {self.parsed_n}, matched {self.matched_n})"]
        if self.missed:
            parts.append("  missed (in golden, not parsed): " + "; ".join(self.missed))
        if self.extra:
            parts.append("  extra (parsed, not in golden): " + "; ".join(self.extra))
        if self.field_misses:
            parts.append("  field mismatches: " + "; ".join(self.field_misses))
        if not self.storage_ok:
            parts.append(f"  storage: {self.storage}")
        return "\n".join(parts)


def score(connector: str, case: str, expected: list[dict], postings: list[Posting],
          storage: dict) -> FixtureResult:
    """Pair `postings` to `expected` by content key, then tally recall/precision + per-field."""
    ambiguous = _ambiguous_urls(expected)
    parsed_by_key: dict[str, list[Posting]] = defaultdict(list)
    for p in postings:
        parsed_by_key[_posting_key(p, ambiguous)].append(p)

    used: set[int] = set()
    fields = {f: [0, 0] for f in FIELDS}
    missed: list[str] = []
    field_misses: list[str] = []
    matched_n = 0

    for g in expected:
        k = _golden_key(g, ambiguous)
        cands = [p for p in parsed_by_key.get(k, []) if id(p) not in used]
        if not cands:
            missed.append(f"{g.get('company','?')} — {g.get('title','?')}")
            continue
        p = cands[0]
        used.add(id(p))
        matched_n += 1
        for fname in FIELDS:
            fields[fname][1] += 1
            if _field_ok(fname, g, p):
                fields[fname][0] += 1
            else:
                got = {"description_present": bool(p.description)}.get(
                    fname, getattr(p, fname, "?"))
                want = g.get(fname)
                field_misses.append(f"{g.get('title','?')}: {fname} = {got!r} (want {want!r})")

    extra = [f"{p.company} — {p.title}" for p in postings if id(p) not in used]
    return FixtureResult(
        connector=connector, case=case, expected_n=len(expected), parsed_n=len(postings),
        matched_n=matched_n, fields={f: tuple(v) for f, v in fields.items()},
        storage=storage, missed=missed, extra=extra, field_misses=field_misses,
    )
