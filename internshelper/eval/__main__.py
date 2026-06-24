"""`python -m internshelper.eval` — print the connector accuracy scorecard.

    python -m internshelper.eval                  # full scorecard table
    python -m internshelper.eval --connector markdown
    python -m internshelper.eval --json           # machine-readable (dashboard/CI)
    python -m internshelper.eval --check          # exit 1 if any fixture < 100%
    python -m internshelper.eval --write PATH     # dump the JSON scorecard to a file
    python -m internshelper.eval --emit-skeleton tests/fixtures/eval/<c>/<case>/golden.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from internshelper.eval import harness


def _pct(v: float | None) -> str:
    return "  — " if v is None else f"{v * 100:3.0f}%"


def _result_dict(r) -> dict:
    return {
        "connector": r.connector, "case": r.case,
        "expected": r.expected_n, "parsed": r.parsed_n, "matched": r.matched_n,
        "recall": r.recall, "precision": r.precision,
        "fields": {f: r.field_acc(f) for f in r.fields},
        "storage_ok": r.storage_ok, "storage": r.storage,
        "error": r.error, "perfect": r.is_perfect(),
        "missed": r.missed, "extra": r.extra, "field_misses": r.field_misses,
    }


def _print_table(results) -> None:
    cols = ["connector", "case", "exp", "got", "rec", "prec",
            "title", "comp", "loc", "url", "date", "desc", "store"]
    widths = [11, 26, 4, 4, 5, 5, 5, 5, 5, 5, 5, 5, 6]
    header = "  ".join(c.ljust(w) for c, w in zip(cols, widths))
    print(header)
    print("-" * len(header))
    fkeys = ["title", "company", "location", "url", "posted_at", "description_present"]
    for r in results:
        store_cell = "ok" if r.storage_ok else "BAD"
        row = [
            r.connector, r.case, str(r.expected_n), str(r.parsed_n),
            _pct(r.recall), _pct(r.precision),
            *[_pct(r.field_acc(f)) for f in fkeys],
            store_cell,
        ]
        line = "  ".join(str(c).ljust(w) for c, w in zip(row, widths))
        if not r.is_perfect():
            line += "  ✗"
        print(line)
    n = len(results)
    perfect = sum(1 for r in results if r.is_perfect())
    if n:
        macro_recall = sum(r.recall for r in results) / n
        macro_prec = sum(r.precision for r in results) / n
        print("-" * len(header))
        print(f"OVERALL  fixtures={n}  perfect={perfect}/{n}  "
              f"recall={macro_recall:.0%}  precision={macro_prec:.0%}")
    for r in results:  # detail block for anything not perfect
        if not r.is_perfect():
            print("\n" + r.diff())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="internshelper.eval",
                                 description="Connector extraction-accuracy scorecard.")
    ap.add_argument("--connector", default=None, help="only score this connector")
    ap.add_argument("--fixtures", default=None, help="override the fixtures root dir")
    ap.add_argument("--json", action="store_true", help="emit the scorecard as JSON")
    ap.add_argument("--check", action="store_true", help="exit 1 if any fixture is < 100%%")
    ap.add_argument("--write", default=None, help="write the JSON scorecard to this path")
    ap.add_argument("--emit-skeleton", default=None,
                    help="re-emit a golden.json's `expected` from the current parser, to stdout")
    args = ap.parse_args(argv)

    if args.emit_skeleton:
        golden = harness.emit_skeleton(Path(args.emit_skeleton))
        print(json.dumps(golden, indent=2, ensure_ascii=False))
        return 0

    root = Path(args.fixtures) if args.fixtures else None
    results = harness.score_all(root=root, connector=args.connector)
    payload = [_result_dict(r) for r in results]

    if args.write:
        Path(args.write).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        if not results:
            print("no fixtures found.")
        else:
            _print_table(results)

    if args.check:
        bad = [r for r in results if not r.is_perfect()]
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
