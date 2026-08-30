#!/usr/bin/env bash
# Prove the Cloud checkout has the repo skills, graph, and test surfaces used locally.
set -euo pipefail

cloud_repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$cloud_repo_dir"

if [ "${INTERNSHELPER_CLOUD:-}" != "1" ]; then
  echo "ERROR: set INTERNSHELPER_CLOUD=1 before running the Cloud readiness check." >&2
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "ERROR: .venv is missing; run scripts/setup-codex-cloud.sh first." >&2
  exit 1
fi

echo "==> validating repository-scoped pstack skills"
.venv/bin/python - <<'PY'
from pathlib import Path
import json
import re

skills_root = Path(".agents/skills")
expected = {
    "blast-radius",
    "engineering-mode",
    "how",
    "interrogate",
    "principle-boundary-discipline",
    "principle-build-the-lever",
    "principle-experience-first",
    "principle-fix-root-causes",
    "principle-guard-the-context-window",
    "principle-laziness-protocol",
    "principle-minimize-reader-load",
    "principle-prove-it-works",
    "reflect",
    "tdd",
    "technical-writing",
    "unslop",
}

for name in sorted(expected):
    path = skills_root / name / "SKILL.md"
    if not path.is_file():
        raise SystemExit(f"missing vendored skill: {path}")
    text = path.read_text()
    if not text.startswith("---\n") or f"\nname: {name}\n" not in text:
        raise SystemExit(f"invalid skill frontmatter: {path}")

engineering = (skills_root / "engineering-mode" / "SKILL.md").read_text()
references = set(re.findall(r"\$([a-z][a-z0-9-]*)", engineering))
missing = references - expected
if missing:
    raise SystemExit(f"engineering-mode references missing skills: {sorted(missing)}")

adapter = (skills_root / "internshelper-engineering" / "SKILL.md").read_text()
if "$engineering-mode" not in adapter:
    raise SystemExit("internshelper-engineering no longer routes through $engineering-mode")

manifest = json.loads(Path(".agents/vendor/pstack-codex/plugin.json").read_text())
expected_version = "0.1.0+codex.20260827232005"
if manifest.get("version") != expected_version:
    raise SystemExit(f"unexpected pstack-codex version: {manifest.get('version')!r}")
for required in ("LICENSE", "NOTICE"):
    if not (Path(".agents/vendor/pstack-codex") / required).is_file():
        raise SystemExit(f"missing pstack attribution file: {required}")

print(f"validated {len(expected)} pstack skills at {expected_version}")
PY

echo "==> checking the code-review graph branch and commit"
cloud_graph_status="$(.venv/bin/code-review-graph status --repo "$cloud_repo_dir")"
echo "$cloud_graph_status"
cloud_branch="$(git branch --show-current)"
cloud_commit="$(git rev-parse --short=12 HEAD)"
grep -Fq "Built on branch: $cloud_branch" <<<"$cloud_graph_status"
grep -Fq "Built at commit: $cloud_commit" <<<"$cloud_graph_status"

echo "==> running the default offline suite"
.venv/bin/python -m pytest -q

echo "==> replaying the frozen availability baseline"
.venv/bin/python -m pytest -q -m availability_baseline

echo "==> checking the future availability contract"
.venv/bin/python -m pytest -q -m availability_contract

echo "==> running headless browser checks"
.venv/bin/python -m pytest -q -m browser

echo "==> Codex Cloud readiness verified"
