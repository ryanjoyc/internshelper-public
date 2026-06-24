"""Deterministic, offline eval harness: replay a saved source response through its real
connector and score the result against a hand-labeled golden.

The injection seam is **parse-direct**: every connector splits `fetch()` (network) from
`parse(data)` (pure), and all connector-side filtering (Ashby `isListed`, GitHub
`active/is_visible`, Lever `categories`, the markdown table logic) already lives in
`parse()`. So feeding a saved payload straight to `parse()` exercises the true extraction
path with zero network and full determinism. (Markdown's year-inference needs a frozen
`now`, taken from each fixture's `as_of`.) A future `seam: "fetch_pages"` mode — for a
pagination audit or a live-drift check — patches the HTTP boundary instead; that branch is
present but unused in v1.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from internshelper import db, store
from internshelper.config import SourceEntry
from internshelper.connectors import base, build_connector
from internshelper.eval import scoring
from internshelper.models import Posting

# Repo-default fixture root. Each fixture is <root>/<connector>/<case>/golden.json.
FIXTURES_ROOT = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "eval"
# Fixed clock for the storage round-trip (timestamps must be reproducible, never wall-clock).
AS_OF = "2026-06-18T00:00:00+00:00"


@dataclass
class Fixture:
    directory: Path
    connector: str
    case: str
    source: dict           # SourceEntry kwargs (type, token, label, columns?)
    payload_path: Path
    seam: str              # "parse" (default) or "fetch_pages"
    as_of: str | None
    expected: list[dict]

    @property
    def id(self) -> str:
        return f"{self.connector}/{self.case}"

    @property
    def as_of_dt(self) -> datetime | None:
        return datetime.fromisoformat(self.as_of) if self.as_of else None

    def load_payload(self):
        text = self.payload_path.read_text(encoding="utf-8")
        return text if self.source.get("type") == "markdown" else json.loads(text)


def _resolve_payload(directory: Path, golden: dict) -> Path:
    ref = golden.get("payload_ref")
    if ref:
        return (directory / ref).resolve()
    for name in ("payload.json", "payload.md"):
        cand = directory / name
        if cand.exists():
            return cand
    raise FileNotFoundError(f"{directory}: no payload_ref and no payload.json/payload.md")


def load_fixture(golden_path: Path) -> Fixture:
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    directory = golden_path.parent
    return Fixture(
        directory=directory,
        connector=golden["connector"],
        case=directory.name,
        source=golden["source"],
        payload_path=_resolve_payload(directory, golden),
        seam=golden.get("seam", "parse"),
        as_of=golden.get("as_of"),
        expected=golden.get("expected", []),
    )


def discover_fixtures(root: Path | None = None, connector: str | None = None) -> list[Fixture]:
    root = root or FIXTURES_ROOT
    if not root.exists():
        return []
    fixtures = [load_fixture(p) for p in sorted(root.glob("*/*/golden.json"))]
    if connector:
        fixtures = [f for f in fixtures if f.connector == connector]
    return fixtures


def run_connector(fx: Fixture) -> list[Posting]:
    """Replay a fixture's payload through its real connector (offline) and return postings."""
    entry = SourceEntry(**fx.source)
    conn = build_connector(entry)
    payload = fx.load_payload()
    if fx.seam == "fetch_pages":
        pages = iter(payload)  # payload is a list of page bodies, replayed in order
        original = base.Connector._get
        base.Connector._get = lambda self, *a, **k: next(pages)  # type: ignore[assignment]
        try:
            return conn.fetch()
        finally:
            base.Connector._get = original  # type: ignore[assignment]
    if entry.type == "markdown":
        return conn.parse(payload, now=fx.as_of_dt)
    return conn.parse(payload)


def storage_roundtrip(postings: list[Posting]) -> dict:
    """Push postings through `store.upsert` into a throwaway DB and assert dedup integrity:
    re-upserting is idempotent (N rows, not 2N) and no two distinct postings silently merge
    onto the same posting_id."""
    with tempfile.TemporaryDirectory() as d:
        conn = db.connect(Path(d) / "t.db")
        db.init_db(conn)
        payloads = Path(d) / "payloads"
        for p in postings:
            store.upsert(conn, p, now=AS_OF, payloads_dir=payloads)
        rows1 = conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0]
        for p in postings:  # second pass — must not grow the table
            store.upsert(conn, p, now=AS_OF, payloads_dir=payloads)
        rows2 = conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0]
        conn.close()
    distinct_ids = len({p.posting_id for p in postings})
    return {
        "idempotent": rows1 == rows2,
        "no_merge": rows1 == distinct_ids == len(postings),
        "rows": rows1,
        "distinct_ids": distinct_ids,
        "n_postings": len(postings),
    }


def score_fixture(fx: Fixture) -> scoring.FixtureResult:
    """Run a fixture through its connector + storage round-trip and score it vs the golden."""
    try:
        postings = run_connector(fx)
    except Exception as e:  # a parse that crashes is itself an accuracy failure (RED)
        return scoring.FixtureResult(
            connector=fx.connector, case=fx.case, expected_n=len(fx.expected),
            parsed_n=0, matched_n=0, fields={f: (0, 0) for f in scoring.FIELDS},
            storage={"idempotent": True, "no_merge": True}, error=f"{type(e).__name__}: {e}",
        )
    storage = storage_roundtrip(postings)
    return scoring.score(fx.connector, fx.case, fx.expected, postings, storage)


def score_all(root: Path | None = None, connector: str | None = None) -> list[scoring.FixtureResult]:
    return [score_fixture(f) for f in discover_fixtures(root, connector)]


def emit_skeleton(golden_path: Path) -> dict:
    """Re-emit a golden with `expected` filled from the CURRENT parser output, preserving all
    other keys (source, payload_ref, as_of, seam). An authoring aid for the happy-path goldens:
    the human eyeballs the emitted rows against the real saved page before trusting them."""
    from internshelper.text import strip_html
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    fx = load_fixture(golden_path)
    postings = run_connector(fx)
    counts: dict[str, int] = {}
    for p in postings:
        u = scoring.canonical_url(p.url)
        if u:
            counts[u] = counts.get(u, 0) + 1
    ambiguous = {u for u, n in counts.items() if n > 1}
    golden["expected"] = [
        {
            "key": scoring._posting_key(p, ambiguous),
            "title": p.title,
            "company": p.company,
            "location": p.location,
            "url": p.url,
            "posted_at": p.posted_at,
            "description_present": bool(strip_html(p.description)),
        }
        for p in postings
    ]
    return golden
