"""Render human-readable availability catalog and coverage reports from corpus.yaml."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from availability.corpus import (  # type: ignore[import-not-found]
        CATALOG_PATH,
        CORPUS_PATH,
        COVERAGE_PATH,
        coverage_gaps,
        coverage_index,
        load_corpus,
        policy_rule_index,
    )
    from availability.mutants import caught_case_ids  # type: ignore[import-not-found]
else:
    from .corpus import (
        CATALOG_PATH,
        CORPUS_PATH,
        COVERAGE_PATH,
        coverage_gaps,
        coverage_index,
        load_corpus,
        policy_rule_index,
    )
    from .mutants import caught_case_ids


ACTION_LABELS = {
    "apply": "Apply",
    "verify": "Verify and find application",
    "confirm_replacement": "Confirm replacement",
    "open_original": "Open Original",
    "none": "None",
}


def _display(value: Any) -> str:
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return " → ".join(str(item).replace("_", " ") for item in value) or "None"
    return str(value).replace("_", " ")


def _case_link(case_id: str) -> str:
    return f"[`{case_id}`](availability-verification-catalog.md#{case_id})"


def render_catalog(corpus: dict[str, Any]) -> str:
    lines = [
        "# Availability verification case catalog",
        "",
        "This file is generated from `tests/availability/corpus.yaml`. Edit the YAML and run",
        "`.venv/bin/python tests/availability/generate.py`; do not edit this catalog directly.",
        "",
        f"- Corpus schema: `{corpus['schema_version']}`",
        f"- Policy version: `{corpus['policy_version']}`",
        f"- Approval: `{corpus['approval']['status']}` by {corpus['approval']['approved_by']} "
        f"on `{corpus['approval']['approved_on']}`",
        f"- Cases: `{len(corpus['cases'])}`",
        "- Scope: approved behavior contract; implementation progress is reported by the contract suite",
        "",
        "## How to read a case",
        "",
        "The timeline states what was observed and in what order. The expected result is the approved",
        "Board contract. A replacement candidate never changes the stored URL unless the",
        "case explicitly says confirmation occurred.",
        "",
        "## Cases",
        "",
    ]

    for case in corpus["cases"]:
        provenance = case["provenance"]
        expected = case["expected"]
        investigation = expected["investigation"]
        replacement = expected["replacement"]
        lines.extend(
            [
                f"### `{case['id']}`",
                "",
                case["description"],
                "",
                f"- Category: `{case['category']}`",
                f"- Provenance: `{provenance['kind']}` — {provenance['note']}",
                f"- Source: `{case['source']['authority']}` / `{case['source']['kind']}` "
                f"({case['source']['label']})",
                f"- User state: `{case['user_state']}`",
                f"- Contract layers: {', '.join(f'`{layer}`' for layer in case['layers'])}",
                "",
                "| # | When | Stage | Observer | Result | Evidence |",
                "|---:|---|---|---|---|---|",
            ]
        )
        for event in case["evidence_timeline"]:
            result = event["result"]
            if event.get("status") is not None:
                result = f"HTTP {event['status']} / {result}"
            evidence = ", ".join(event["signals"])
            lines.append(
                f"| {event['sequence']} | {event['at']} | `{event['stage']}` | "
                f"`{event['channel']}` | {_display(result)} | "
                f"{event['note']} (`{evidence}`) |"
            )
        lines.extend(
            [
                "",
                "| Expected field | Value |",
                "|---|---|",
                f"| Availability | `{expected['availability']}` |",
                f"| Board treatment | `{expected['board_treatment']}` |",
                f"| Primary action | {ACTION_LABELS[expected['primary_action']]} |",
                f"| Secondary action | {ACTION_LABELS[expected['secondary_action']]} |",
                f"| Investigation result | `{investigation['outcome']}` |",
                f"| Investigation progress | {_display(investigation['stages'])} |",
                f"| Replacement behavior | `{replacement['behavior']}` |",
                f"| Replacement candidate | {_display(replacement.get('candidate_url'))} |",
                f"| Replacement confirmation required | {_display(replacement['requires_confirmation'])} |",
                f"| Stored URL after this case | `{replacement['stored_url']}` |",
                f"| Ranking effect | `{expected['ranking_effect']}` |",
                "",
                f"Why: {case['justification']}",
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def render_coverage(corpus: dict[str, Any]) -> str:
    index = coverage_index(corpus)
    gaps = coverage_gaps(corpus)
    rules = policy_rule_index(corpus)
    lines = [
        "# Availability verification coverage",
        "",
        "This file is generated from `tests/availability/corpus.yaml`. The matrix derives coverage",
        "from each case's source, timeline, user state, expectations, and layers. Cases do not carry",
        "a second handwritten set of coverage tags.",
        "",
        f"- Corpus schema: `{corpus['schema_version']}`",
        f"- Policy version: `{corpus['policy_version']}`",
        f"- Approval: `{corpus['approval']['status']}` by {corpus['approval']['approved_by']} "
        f"on `{corpus['approval']['approved_on']}`",
        f"- Required coverage gaps: `{len(gaps)}`",
        f"- Status: **{'complete' if not gaps else 'incomplete'}**",
        "",
        "## Required dimensions",
        "",
        "| Dimension | Required value | Cases |",
        "|---|---|---|",
    ]
    for dimension, required_values in corpus["coverage_requirements"].items():
        for value in required_values:
            case_ids = index.get(dimension, {}).get(value, [])
            rendered = ", ".join(_case_link(case_id) for case_id in case_ids) or "**GAP**"
            lines.append(f"| `{dimension}` | `{value}` | {rendered} |")

    lines.extend(
        [
            "",
            "## Locked product expectations",
            "",
            "| Rule | Approved expectation | Cases |",
            "|---|---|---|",
        ]
    )
    for rule_id, statement in corpus["policy_rules"].items():
        case_ids = rules.get(rule_id, [])
        rendered = ", ".join(_case_link(case_id) for case_id in case_ids) or "**GAP**"
        lines.append(f"| `{rule_id}` | {statement} | {rendered} |")

    lines.extend(
        [
            "",
            "## Fault-detection scorecard",
            "",
            "A mutant is caught when at least one case expects a different semantic result from the",
            "deliberately wrong policy. The default test suite recomputes this table's underlying data.",
            "",
            "| Mutant | Deliberately wrong behavior | Cases that reject it |",
            "|---|---|---|",
        ]
    )
    for mutant_id, description in corpus["mutants"].items():
        case_ids = caught_case_ids(corpus, mutant_id)
        rendered = ", ".join(_case_link(case_id) for case_id in case_ids) or "**SURVIVED**"
        lines.append(f"| `{mutant_id}` | {description} | {rendered} |")

    lines.extend(
        [
            "",
            "## Maintenance rules",
            "",
        ]
    )
    lines.extend(f"- {rule}" for rule in corpus["maintenance_rules"])

    lines.extend(
        [
            "",
            "## Commands",
            "",
            "```bash",
            "# Offline integrity, generated-doc, fake-component, and mutant checks (default-running)",
            ".venv/bin/python -m pytest tests/test_availability_corpus.py",
            "",
            "# Approved production contract; all 109 layer expectations must pass",
            ".venv/bin/python -m pytest -q -m availability_contract",
            "",
            "# Optional, read-only, informational network probes",
            ".venv/bin/python -m pytest -q -m live_canary -s",
            "```",
            "",
            "The default suite excludes `availability_contract`, `live_canary`, and `browser`. The",
            "canaries report observed HTTP results but never assert that a posting remains live.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def generated_documents(corpus: dict[str, Any]) -> dict[Path, str]:
    return {
        CATALOG_PATH: render_catalog(corpus),
        COVERAGE_PATH: render_coverage(corpus),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if generated files are stale")
    args = parser.parse_args(argv)
    corpus = load_corpus(CORPUS_PATH)
    stale: list[Path] = []
    for path, content in generated_documents(corpus).items():
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                stale.append(path)
        else:
            path.write_text(content, encoding="utf-8")
            print(f"wrote {path.relative_to(CORPUS_PATH.parents[2])}")
    if stale:
        for path in stale:
            print(f"stale: {path.relative_to(CORPUS_PATH.parents[2])}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
