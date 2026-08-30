"""Exercise the approved corpus against internsHELPer's pre-availability behavior.

This is deliberately a test adapter, not production availability policy.  It feeds the
parts of each timeline that today's collector understands through the real SQLite store,
then reports unsupported evidence separately from genuine semantic mismatches.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from availability.corpus import (  # type: ignore[import-not-found]
        CORPUS_PATH,
        expected_semantics,
        load_corpus,
    )
else:
    from .corpus import CORPUS_PATH, expected_semantics, load_corpus

from internshelper import db, store
from internshelper.models import Posting


HERE = Path(__file__).parent
ROOT = HERE.parents[1]
BASELINE_PATH = HERE / "current-baseline.json"
REPORT_PATH = ROOT / "docs" / "availability-current-baseline.md"
BASELINE_REVISION = "a8abdc8"
BASELINE_DATE = "2026-08-30"
ORIGINAL_URL = "https://jobs.example.test/roles/original"
INITIAL_RANK_SCORE = 73.5
STATUSES = {"pass", "mismatch", "unsupported"}


def _posting(case: dict[str, Any], *, replacement: bool = False) -> Posting:
    suffix = "replacement" if replacement else "original"
    candidate_url = case["expected"]["replacement"].get("candidate_url")
    return Posting(
        posting_id=f"baseline:{case['id']}:{suffix}",
        source_key=f"{case['source']['kind']}:baseline_{case['id']}",
        title="Software Engineering Intern" if not replacement else "Software Engineering Intern II",
        company="Baseline Employer",
        location="New York, NY",
        url=candidate_url if replacement and candidate_url else ORIGINAL_URL,
        description="Deterministic baseline fixture",
        is_internship=True,
        is_cs_relevant=True,
    )


def _unsupported_capabilities(case: dict[str, Any]) -> list[str]:
    """Name absent mechanisms needed to evaluate this case, even if outputs coincide."""

    events = case["evidence_timeline"]
    signals = {signal for event in events for signal in event["signals"]}
    stages = {event["stage"] for event in events}
    expected = case["expected"]
    missing: set[str] = set()

    if any(event["channel"] == "network" for event in events):
        missing.add("destination_validation")
    if "community_pre_display_check" in case["policy_rules"]:
        missing.add("community_pre_display_validation")
    if expected["availability"] == "uncertain":
        missing.add("tri_state_availability")
    if "scheduled_retry" in stages or any(event["attempt"] > 1 for event in events):
        missing.add("evidence_history_and_retries")
    if signals & {"employer_closure_text", "generic_error_text", "unusual_title"}:
        missing.add("content_aware_closure")
    if signals & {
        "multiple_source_observations",
        "conflicting_evidence",
        "community_presence",
        "community_removal",
    }:
        missing.add("cross_source_reconciliation")
    if expected["board_treatment"] in {"warned", "guarded"}:
        missing.add("availability_board_treatments")
    if expected["primary_action"] == "verify":
        missing.add("verify_action")
    if any(event["channel"] == "investigator" for event in events):
        missing.add("investigation_workflow")
    if expected["replacement"]["behavior"] != "none":
        missing.add("replacement_confirmation")
    return sorted(missing)


def _apply_source_event(
    conn, case: dict[str, Any], original: Posting, event: dict[str, Any], now: str
) -> None:
    """Translate only source-enumeration evidence into today's real store operations."""

    result = event["result"]
    if result == "present":
        if "replacement_url" in event["signals"]:
            # Today's collector can discover this as another posting, but it cannot relate
            # the candidate to the original or ask for replacement confirmation.
            store.upsert(conn, _posting(case, replacement=True), now=now)
        else:
            store.upsert(conn, original, now=now)
        return
    if result in {"absent", "removed"}:
        # A successful, non-empty enumeration that omitted the original closes it at once.
        store.apply_close_detection(
            conn,
            original.source_key,
            seen_ids={f"baseline:{case['id']}:other"},
            ok=True,
            count=1,
        )
        return
    if result == "source_error":
        store.apply_close_detection(
            conn, original.source_key, seen_ids=set(), ok=False, count=0
        )
        return
    raise AssertionError(f"unmapped source result in approved corpus: {result}")


def _observe_case(case: dict[str, Any], db_path: Path) -> tuple[dict[str, Any], list[int], list[int]]:
    conn = db.connect(db_path)
    try:
        db.init_db(conn)
        original = _posting(case)
        store.upsert(conn, original, now="2026-08-30T12:00:00+00:00")
        conn.execute(
            "UPDATE postings SET rank_score = ? WHERE posting_id = ?",
            (INITIAL_RANK_SCORE, original.posting_id),
        )
        conn.commit()

        if case["user_state"] == "interested":
            store.set_application(conn, original.posting_id, status="Interested")
        elif case["user_state"] == "applied":
            store.set_application(
                conn, original.posting_id, status="Applied", applied_date="2026-08-29"
            )

        consumed: list[int] = []
        ignored: list[int] = []
        for event in case["evidence_timeline"]:
            if event["channel"] == "source":
                _apply_source_event(
                    conn,
                    case,
                    original,
                    event,
                    now=f"2026-08-30T12:{event['sequence']:02d}:00+00:00",
                )
                consumed.append(event["sequence"])
            else:
                ignored.append(event["sequence"])

        row = conn.execute(
            "SELECT * FROM postings WHERE posting_id = ?", (original.posting_id,)
        ).fetchone()
        board_rows = {item["posting_id"]: item for item in store.inbox_with_status(conn)}
        visible = original.posting_id in board_rows
        active = bool(row["is_active"])
        current_rank = row["rank_score"]

        # The existing primary drawer link is labelled "Open original posting".  For the
        # contract comparison it is the direct application path, so it maps to semantic
        # action `apply`; there is no availability-dependent guard or Verify action.
        observed = {
            "availability": "live" if active else "closed",
            "board_treatment": "normal" if active else "visible_closed",
            "primary_action": "apply" if visible else "none",
            "secondary_action": "none",
            "ranking_effect": "none" if current_rank == INITIAL_RANK_SCORE else "changed",
            "investigation_outcome": "not_run",
            "investigation_stages": [],
            "replacement_behavior": "none",
            "replacement_requires_confirmation": False,
            "replacement_candidate_url": None,
            "stored_url": "original" if row["url"] == ORIGINAL_URL else "candidate",
        }
        return observed, consumed, ignored
    finally:
        conn.close()


def _run_case(case: dict[str, Any], db_path: Path) -> dict[str, Any]:
    expected = expected_semantics(case)
    observed, consumed, ignored = _observe_case(case, db_path)
    mismatched = sorted(key for key, value in expected.items() if observed[key] != value)
    unsupported = _unsupported_capabilities(case)
    status = "mismatch" if mismatched else "unsupported" if unsupported else "pass"
    return {
        "case_id": case["id"],
        "status": status,
        "expected": expected,
        "observed": observed,
        "mismatched_fields": mismatched,
        "unsupported_capabilities": unsupported,
        "consumed_evidence": consumed,
        "ignored_evidence": ignored,
    }


def run_baseline(corpus: dict[str, Any], *, work_dir: Path) -> dict[str, Any]:
    """Run all cases in isolated SQLite files beneath caller-owned temporary storage."""

    work_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for case in corpus["cases"]:
        handle = tempfile.NamedTemporaryFile(
            prefix=f"availability-{case['id']}-", suffix=".db", dir=work_dir, delete=False
        )
        handle.close()
        results.append(_run_case(case, Path(handle.name)))
    summary = {status: sum(result["status"] == status for result in results) for status in sorted(STATUSES)}
    return {
        "schema_version": 1,
        "baseline_name": "pre_availability_implementation",
        "baseline_revision": BASELINE_REVISION,
        "generated_on": BASELINE_DATE,
        "corpus_policy_version": corpus["policy_version"],
        "corpus_approval": corpus["approval"],
        "classification": {
            "pass": "Current code consumes the relevant evidence and matches every approved semantic field.",
            "mismatch": "At least one user-visible or persisted semantic field differs from the approved result.",
            "unsupported": "Outputs happen to match, but current code does not consume a capability required to establish them.",
        },
        "summary": summary,
        "results": results,
    }


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _compact(values: dict[str, Any]) -> str:
    return "/".join(
        str(values[key])
        for key in ("availability", "board_treatment", "primary_action")
    )


def render_report(baseline: dict[str, Any]) -> str:
    summary = baseline["summary"]
    lines = [
        "# Current availability implementation baseline",
        "",
        "This report compares the approved availability corpus with the implementation that",
        f"existed at production-code revision `{baseline['baseline_revision']}`. It is generated",
        "from `tests/availability/current-baseline.json`; do not edit it directly.",
        "",
        f"- Run date: `{baseline['generated_on']}`",
        f"- Approved policy: `{baseline['corpus_policy_version']}`",
        f"- Cases: `{sum(summary.values())}`",
        f"- Pass: **{summary['pass']}**",
        f"- Mismatch: **{summary['mismatch']}**",
        f"- Unsupported: **{summary['unsupported']}**",
        "- Database safety: every case ran in a fresh temporary SQLite database",
        "",
        "## What the labels mean",
        "",
    ]
    for status in ("pass", "mismatch", "unsupported"):
        lines.append(f"- **{status.title()}:** {baseline['classification'][status]}")
    lines.extend(
        [
            "",
            "The current Board's `Open original posting` button is mapped to the contract's",
            "semantic `apply` action because both are the direct destination path. Current code",
            "does not vary that link based on availability.",
            "",
            "## Case results",
            "",
            "| Case | Result | Approved availability/treatment/action | Current availability/treatment/action | Detail |",
            "|---|---|---|---|---|",
        ]
    )
    for result in baseline["results"]:
        details: list[str] = []
        if result["mismatched_fields"]:
            details.append("differs: " + ", ".join(result["mismatched_fields"]))
        if result["unsupported_capabilities"]:
            details.append("missing: " + ", ".join(result["unsupported_capabilities"]))
        if result["ignored_evidence"]:
            details.append(
                "ignored timeline steps: "
                + ", ".join(str(item) for item in result["ignored_evidence"])
            )
        lines.append(
            f"| `{result['case_id']}` | **{result['status']}** | "
            f"`{_compact(result['expected'])}` | `{_compact(result['observed'])}` | "
            f"{'<br>'.join(details) or 'No differences'} |"
        )
    lines.extend(
        [
            "",
            "## Reproduce",
            "",
            f"At production-code revision `{baseline['baseline_revision']}`:",
            "",
            "```bash",
            ".venv/bin/python tests/availability/current_baseline.py --check",
            ".venv/bin/python -m pytest -m availability_baseline -q",
            "```",
            "",
            "This command is offline and read-only with respect to configured sources, the real",
            "database, saved postings, and application state.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _serialized(baseline: dict[str, Any]) -> str:
    return json.dumps(baseline, indent=2, sort_keys=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="refresh the checked-in snapshot and report")
    mode.add_argument("--check", action="store_true", help="compare current behavior with the snapshot")
    args = parser.parse_args(argv)

    corpus = load_corpus(CORPUS_PATH)
    with tempfile.TemporaryDirectory(prefix="internshelper-availability-baseline-") as temp_dir:
        baseline = run_baseline(corpus, work_dir=Path(temp_dir))
    serialized = _serialized(baseline)
    report = render_report(baseline)
    if args.write:
        BASELINE_PATH.write_text(serialized, encoding="utf-8")
        REPORT_PATH.write_text(report, encoding="utf-8")
        print(
            f"wrote {BASELINE_PATH} and {REPORT_PATH}: "
            + ", ".join(f"{key}={value}" for key, value in baseline["summary"].items())
        )
        return 0

    stale = []
    if not BASELINE_PATH.is_file() or BASELINE_PATH.read_text(encoding="utf-8") != serialized:
        stale.append(str(BASELINE_PATH))
    if not REPORT_PATH.is_file() or REPORT_PATH.read_text(encoding="utf-8") != report:
        stale.append(str(REPORT_PATH))
    if stale:
        print("baseline differs from current implementation: " + ", ".join(stale))
        return 1
    print(
        "baseline matches current implementation: "
        + ", ".join(f"{key}={value}" for key, value in baseline["summary"].items())
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
