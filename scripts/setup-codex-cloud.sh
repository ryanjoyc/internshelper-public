#!/usr/bin/env bash
# Reproduce the internsHELPer engineering environment inside Codex Cloud.
# This intentionally skips internshelper.setup: Cloud must not create .env,
# install launchd jobs, send email, or point at the user's real SQLite database.
set -euo pipefail

cloud_repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$cloud_repo_dir"

cloud_stage="setup"
if [ "${1:-}" = "--maintenance" ]; then
  cloud_stage="maintenance"
elif [ "$#" -ne 0 ]; then
  echo "Usage: bash scripts/setup-codex-cloud.sh [--maintenance]" >&2
  exit 2
fi

cloud_python="${INTERNSHELPER_CLOUD_PYTHON:-python3}"
if ! "$cloud_python" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 1)' 2>/dev/null; then
  echo "ERROR: Codex Cloud must be configured with Python 3.13." >&2
  exit 1
fi

if [ -d .venv ]; then
  if ! .venv/bin/python -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 1)' 2>/dev/null; then
    echo "ERROR: the cached .venv is not Python 3.13; reset the Codex Cloud environment cache." >&2
    exit 1
  fi
else
  "$cloud_python" -m venv .venv
fi

echo "==> installing internsHELPer engineering dependencies ($cloud_stage)"
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install -e ".[ui]"
.venv/bin/python -m pip install "code-review-graph==2.3.2"

echo "==> installing Chromium for headless Board checks"
if [ "$cloud_stage" = "setup" ] && [ "$(uname -s)" = "Linux" ]; then
  .venv/bin/python -m playwright install --with-deps chromium
else
  .venv/bin/python -m playwright install chromium
fi

echo "==> rebuilding the code-review graph for $(git branch --show-current)"
.venv/bin/code-review-graph build --repo "$cloud_repo_dir"

echo "==> Codex Cloud environment ready"
