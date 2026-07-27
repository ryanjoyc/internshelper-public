"""Graded term classifier — "is this posting my target term (e.g. Summer 2027)?".

Mirrors the manual one-off run we did over speedyapply: read each posting's text, decide a
graded verdict with evidence, and accept an honest gray zone. Two layers:

- **Tier-0 (this module, free, deterministic, offline):** `parse_term` extracts season/year
  signals from title + description; `grade` maps them to a verdict against a configurable
  target. Clear cases resolve here (explicit Summer 2027 -> EXPLICIT; an explicit 2026 / fall
  term -> NOT); the ambiguous remainder lands POSSIBLE/UNREADABLE.
- **Tier-1 (the `/classify-terms` skill or `--llm`):** an LLM re-judges only the
  POSSIBLE/UNREADABLE rows. Free when run by the human in an interactive Claude Code session;
  pay-per-token if the app calls a model itself (`--llm`, opt-in).

The CLI (classify / list-candidates / set-term / summary) deliberately parallels
`internshelper.review`. Verdicts are written via `store.set_term`.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field

from internshelper import clock, config, db, enrich, store
from internshelper.dotenv import load_dotenv
from internshelper.text import strip_html

# Graded verdicts, strongest-positive to no-answer.
TERM_VERDICTS = ("EXPLICIT", "LIKELY", "POSSIBLE", "NOT", "UNREADABLE")
SEASONS = ("winter", "spring", "summer", "fall")
_SEASON_ALIAS = {"autumn": "fall"}
# Month number -> season (meteorological-ish; good enough to read an internship term).
_MONTH_SEASON = {
    12: "winter", 1: "winter", 2: "winter",
    3: "spring", 4: "spring", 5: "spring",
    6: "summer", 7: "summer", 8: "summer",
    9: "fall", 10: "fall", 11: "fall",
}
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_SEASON_RE = "|".join([*SEASONS, *_SEASON_ALIAS])
_SEASON_YEAR = re.compile(rf"\b({_SEASON_RE})\b[\s\-,/]*'?((?:20)?\d{{2}})\b", re.I)
_YEAR_SEASON = re.compile(rf"\b((?:20)?\d{{2}})[\s\-,/]*({_SEASON_RE})\b", re.I)
_SEASON_WORD = re.compile(rf"\b({_SEASON_RE})\b", re.I)
_YEAR4 = re.compile(r"\b(20\d{2})\b")
_CY = re.compile(r"\bcy[\s\-]?(\d{2})\b", re.I)
# Month, an optional day ("October 5, 2026"), then a 4-digit year.
_MONTH_YEAR = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")\b\.?\s+(?:\d{1,2}(?:st|nd|rd|th)?,?\s+)?(20\d{2})\b", re.I
)

# Bare years are only trusted inside a plausible recruiting window relative to the target,
# so "founded in 2010" or a street number can't masquerade as a cycle year.
_YEAR_WINDOW_BACK = 2


def _norm_year(raw: str) -> int:
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 4:
        return int(digits)
    return 2000 + int(digits[-2:])


def _norm_season(raw: str) -> str:
    s = raw.lower()
    return _SEASON_ALIAS.get(s, s)


@dataclass
class TermSignals:
    """What `parse_term` found in a posting's text."""
    season_years: list[tuple[str, int]] = field(default_factory=list)  # explicit "Summer 2027"
    start_terms: list[tuple[str, int]] = field(default_factory=list)   # from month-year dates
    years: list[int] = field(default_factory=list)                     # any 4-digit / CY year
    seasons: list[str] = field(default_factory=list)                   # season words present

    def concrete_terms(self) -> list[tuple[str, int]]:
        """Explicit (season, year) terms — co-occurrences and date-derived starts."""
        out: list[tuple[str, int]] = []
        for t in [*self.season_years, *self.start_terms]:
            if t not in out:
                out.append(t)
        return out


def parse_term(text: str) -> TermSignals:
    """Extract season/year term signals from already-plain text (title + stripped JD)."""
    sig = TermSignals()
    for rx, season_first in ((_SEASON_YEAR, True), (_YEAR_SEASON, False)):
        for m in rx.finditer(text):
            season = _norm_season(m.group(1) if season_first else m.group(2))
            year = _norm_year(m.group(2) if season_first else m.group(1))
            if (season, year) not in sig.season_years:
                sig.season_years.append((season, year))

    # Month-year dates -> the EARLIEST is the term start (a co-op "Oct 2026 - Apr 2027" starts
    # in fall 2026). Collect all, keep the season+year of the earliest.
    dates = sorted(
        (_norm_year(y), _MONTHS[mon.lower()]) for mon, y in _MONTH_YEAR.findall(text)
    )
    if dates:
        yr, mo = dates[0]
        sig.start_terms.append((_MONTH_SEASON[mo], yr))

    years = {int(y) for y in _YEAR4.findall(text)} | {_norm_year(y) for y in _CY.findall(text)}
    sig.years = sorted(years)
    sig.seasons = sorted({_norm_season(s) for s in _SEASON_WORD.findall(text)})
    return sig


@dataclass
class Verdict:
    verdict: str
    season: str | None
    year: int | None
    evidence: str
    source: str = "heuristic"


def grade(
    sig: TermSignals,
    target: tuple[str, int],
    *,
    has_description: bool,
    term_eligible: bool,
) -> Verdict:
    """Map extracted signals to a graded verdict against `target` = (season, year).

    `term_eligible` is False for a new-grad/full-time role: a season is an internship-term
    concept, so such a role is NOT the target term by definition. `has_description` False (we
    never got any text) downgrades "no signal" from POSSIBLE to UNREADABLE.
    """
    tseason, tyear = target
    if not term_eligible:
        return Verdict("NOT", None, None,
                       "new-grad / full-time role — not an internship-term posting")

    concrete = sig.concrete_terms()
    # 1. Explicit target term anywhere -> EXPLICIT.
    if (tseason, tyear) in concrete:
        return Verdict("EXPLICIT", tseason, tyear, f"explicit {tseason.title()} {tyear} in text")
    # 2. A different explicit term (e.g. Fall 2026, or a co-op start date) -> NOT.
    if concrete:
        s, y = concrete[0]
        return Verdict("NOT", s, y, f"explicit {s.title()} {y} term (target is {tseason.title()} {tyear})")

    window = range(tyear - _YEAR_WINDOW_BACK, tyear + 1)
    in_window = [y for y in sig.years if y in window]
    # 3. LIKELY: target year present + the target season word appears (not adjacent enough to
    #    be a clean co-occurrence, but both signals are there).
    if tyear in sig.years and tseason in sig.seasons:
        return Verdict("LIKELY", tseason, tyear,
                       f"{tseason} and {tyear} both present (not adjacent)")
    # 4. A different in-window cycle year (e.g. req 'R2026', '2026 Start') and NOT the target
    #    year -> NOT (this is a 2026-cycle posting, target is 2027).
    others = [y for y in in_window if y != tyear]
    if others and tyear not in sig.years:
        return Verdict("NOT", None, others[0],
                       f"{others[0]} cycle year present, no {tyear} signal")
    # 5. Target year present but no season at all -> weak positive.
    if tyear in sig.years:
        return Verdict("LIKELY", tseason, tyear, f"{tyear} present, season unstated")
    # 6. No usable term signal.
    if not has_description:
        return Verdict("UNREADABLE", None, None, "no readable description to classify")
    return Verdict("POSSIBLE", None, None,
                   "internship with no stated term — target not excluded, not shown")


def classify_text(
    title: str,
    description: str,
    target: tuple[str, int],
    *,
    term_eligible: bool,
) -> Verdict:
    """Convenience: parse + grade over a posting's title + description."""
    desc = strip_html(description or "")
    text = f"{title or ''} {desc}".strip()
    sig = parse_term(text)
    return grade(sig, target, has_description=bool(desc), term_eligible=term_eligible)


# --- target parsing -------------------------------------------------------------------

def parse_target(spec: str) -> tuple[str, int]:
    """'summer:2027' / 'summer 2027' / 'summer-2027' -> ('summer', 2027)."""
    m = re.match(rf"\s*({_SEASON_RE})\s*[:\s\-]\s*((?:20)?\d{{2}})\s*$", spec, re.I)
    if not m:
        raise ValueError(f"bad --target {spec!r}; expected like 'summer:2027'")
    return _norm_season(m.group(1)), _norm_year(m.group(2))


def default_target(settings: config.Settings) -> tuple[str, int]:
    return (settings.term_season, settings.term_year)


# --- CLI ------------------------------------------------------------------------------

_SELECT = (
    "posting_id, title, company, location, url, description, payload_path, "
    "is_internship, is_newgrad, term_verdict, term_season, term_year, term_evidence"
)


def _term_eligible(row: sqlite3.Row) -> bool:
    """A season is an internship-term concept: intern/co-op qualifies, new-grad-only does not."""
    return bool(row["is_internship"])


def select_for_classify(
    conn: sqlite3.Connection, *, only_active: bool, reclassify: bool, limit: int | None
) -> list[sqlite3.Row]:
    clauses = []
    if only_active:
        clauses.append("is_active = 1")
    if not reclassify:
        clauses.append("term_verdict IS NULL")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (
        f"SELECT {_SELECT} FROM postings{where} "
        "ORDER BY (is_internship = 1) DESC, (posted_at IS NULL), posted_at DESC, first_seen DESC"
    )
    rows = conn.execute(sql).fetchall()
    return rows[:limit] if limit else rows


def classify_pass(
    conn: sqlite3.Connection,
    target: tuple[str, int],
    *,
    only_active: bool = True,
    reclassify: bool = False,
    limit: int | None = None,
    now: str | None = None,
    get_json=enrich._default_get_json,
) -> dict[str, int]:
    """Tier-0 pass: enrich-when-missing, grade, write term_* columns. Returns a verdict tally."""
    now = now or clock.now_iso()
    tally = {v: 0 for v in TERM_VERDICTS}
    tally["enriched"] = 0
    for row in select_for_classify(conn, only_active=only_active, reclassify=reclassify, limit=limit):
        description = row["description"] or ""
        if not strip_html(description).strip() and row["url"]:
            fetched = enrich.description_for(row["url"], get_json=get_json)
            if fetched:
                store.update_description(conn, row["posting_id"], fetched)
                description = fetched
                tally["enriched"] += 1
        v = classify_text(row["title"], description, target, term_eligible=_term_eligible(row))
        store.set_term(
            conn, row["posting_id"], verdict=v.verdict, season=v.season, year=v.year,
            evidence=v.evidence, source=v.source, now=now,
        )
        tally[v.verdict] += 1
    return tally


def list_candidates(conn: sqlite3.Connection, limit: int | None = None) -> list[dict]:
    """POSSIBLE / UNREADABLE rows — the Tier-1 (agent/LLM) work queue, freshest first."""
    sql = (
        f"SELECT {_SELECT}, posted_at, first_seen FROM postings "
        "WHERE term_verdict IN ('POSSIBLE', 'UNREADABLE') AND is_active = 1 "
        "ORDER BY (posted_at IS NULL), posted_at DESC, first_seen DESC, posting_id"
    )
    rows = [dict(r) for r in conn.execute(sql)]
    return rows[:limit] if limit else rows


def summary(conn: sqlite3.Connection, target: tuple[str, int]) -> dict:
    """Counts by verdict + the EXPLICIT/LIKELY shortlist for the target term."""
    counts = {
        r["term_verdict"]: r["n"]
        for r in conn.execute(
            "SELECT term_verdict, COUNT(*) n FROM postings WHERE is_active = 1 "
            "AND term_verdict IS NOT NULL GROUP BY term_verdict"
        )
    }
    shortlist = [
        dict(r)
        for r in conn.execute(
            "SELECT posting_id, title, company, location, url, term_verdict, term_evidence "
            "FROM postings WHERE is_active = 1 AND term_verdict IN ('EXPLICIT','LIKELY') "
            "ORDER BY term_verdict, company, title"
        )
    ]
    return {"target": f"{target[0]}:{target[1]}", "counts": counts, "shortlist": shortlist}


def _open() -> sqlite3.Connection:
    conn = db.connect(config.default_path("INTERNSHELPER_DB", "data/internshelper.db"))
    db.init_db(conn)
    return conn


def _load_settings() -> config.Settings:
    return config.load_settings(config.default_path("INTERNSHELPER_SETTINGS", "config/settings.toml"))


def main(argv=None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="internshelper.term")
    sub = parser.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("classify", help="Tier-0 heuristic pass (enrich-when-missing + grade)")
    c.add_argument("--target", default=None, help="e.g. summer:2027 (default from settings)")
    c.add_argument("--limit", type=int, default=None)
    c.add_argument("--all", action="store_true", help="include inactive/closed postings")
    c.add_argument("--reclassify", action="store_true", help="re-grade already-classified rows")
    c.add_argument("--llm", action="store_true",
                   help="OPT-IN, METERED: also LLM-judge the POSSIBLE/UNREADABLE remainder "
                        "(needs ANTHROPIC_API_KEY; pay-per-token)")

    lc = sub.add_parser("list-candidates", help="POSSIBLE/UNREADABLE queue for agent review (JSON)")
    lc.add_argument("--limit", type=int, default=None)

    st = sub.add_parser("set-term", help="record a graded term verdict for one posting")
    st.add_argument("posting_id")
    st.add_argument("--verdict", required=True, choices=TERM_VERDICTS)
    st.add_argument("--season", default=None, choices=[*SEASONS, "none"])
    st.add_argument("--year", type=int, default=None)
    st.add_argument("--evidence", default="")
    st.add_argument("--source", default="agent", choices=("heuristic", "agent", "api"))

    sm = sub.add_parser("summary", help="verdict counts + EXPLICIT/LIKELY shortlist (JSON)")
    sm.add_argument("--target", default=None)

    args = parser.parse_args(argv)
    conn = _open()
    settings = _load_settings()

    if args.cmd == "classify":
        target = parse_target(args.target) if args.target else default_target(settings)
        tally = classify_pass(
            conn, target, only_active=not args.all, reclassify=args.reclassify, limit=args.limit
        )
        print(json.dumps({"target": f"{target[0]}:{target[1]}", **tally}, indent=2))
        if args.llm:
            rc = _run_llm_upgrade(conn, target, settings)
            if rc != 0:
                return rc
    elif args.cmd == "list-candidates":
        print(json.dumps(list_candidates(conn, args.limit), indent=2))
    elif args.cmd == "set-term":
        season = None if args.season in (None, "none") else args.season
        store.set_term(
            conn, args.posting_id, verdict=args.verdict, season=season, year=args.year,
            evidence=args.evidence, source=args.source, now=clock.now_iso(),
        )
        print(f"{args.posting_id}: {args.verdict}")
    elif args.cmd == "summary":
        target = parse_target(args.target) if args.target else default_target(settings)
        print(json.dumps(summary(conn, target), indent=2))
    return 0


def _run_llm_upgrade(conn: sqlite3.Connection, target, settings: config.Settings) -> int:
    """Opt-in, METERED in-app LLM upgrade of the ambiguous remainder.

    Guarded hard: refuses without a key, and prints the row count + that this bills
    pay-per-token before doing anything. The FREE path is the `/classify-terms` skill, which
    runs in an interactive Claude Code session on the user's subscription instead.
    """
    import os

    if not settings.term_llm_enabled:
        print("term --llm: disabled. Set [term.llm] enabled = true in settings.toml to opt in.",
              file=sys.stderr)
        return 2
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("term --llm: no ANTHROPIC_API_KEY. In-app LLM is pay-per-token and needs a key.\n"
              "Free alternative: run the /classify-terms skill in an interactive Claude Code "
              "session (uses your subscription, no per-token charge).", file=sys.stderr)
        return 2
    candidates = list_candidates(conn)
    print(f"term --llm: {len(candidates)} POSSIBLE/UNREADABLE rows would be judged via "
          f"{settings.term_llm_model}. This bills PER TOKEN to your Anthropic API account.",
          file=sys.stderr)
    try:
        import anthropic  # noqa: F401
    except Exception:
        print("term --llm: the `anthropic` SDK is not installed (pip install anthropic).",
              file=sys.stderr)
        return 2
    # Real metered judging is intentionally left to a follow-up; the free skill lane is primary.
    print("term --llm: SDK present — wire the judging loop here (deferred; use /classify-terms).",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
