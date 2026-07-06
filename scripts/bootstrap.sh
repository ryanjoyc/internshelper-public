#!/usr/bin/env bash
# internsHELPer one-command bootstrap: create the venv + install deps, then hand off to
# `internshelper.setup` to scaffold this machine's .env and (optionally) the launchd schedule.
# Idempotent — safe to re-run; an existing .venv is reused (unless too old) and an existing .env
# is never touched.
#
#   bash scripts/bootstrap.sh                # interactive
#   bash scripts/bootstrap.sh --no-input     # accept defaults / INTERNSHELPER_* env
#   PYTHON=/opt/homebrew/bin/python3.12 bash scripts/bootstrap.sh   # pick the interpreter
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

PY="${PYTHON:-python3}"

require_311() {  # $1 = a python executable; succeeds iff it is >= 3.11
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null
}

if ! require_311 "$PY"; then
  echo "ERROR: internsHELPer needs Python >= 3.11, but '$PY' is older (or not found)." >&2
  echo "       Install 3.11+ and re-run, e.g.: PYTHON=/opt/homebrew/bin/python3.12 bash scripts/bootstrap.sh" >&2
  exit 1
fi

# Reuse an existing .venv only if its interpreter is still >= 3.11; otherwise recreate it so a
# first-run failure on an old python isn't cached forever.
if [ -d ".venv" ] && require_311 ".venv/bin/python"; then
  echo "==> reusing existing .venv"
else
  [ -d ".venv" ] && { echo "==> existing .venv is unusable (< 3.11) — recreating"; rm -rf .venv; }
  echo "==> creating .venv"
  "$PY" -m venv .venv
fi

echo "==> installing core engine + web UI + dev deps"
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -e ".[dev,web]"

# Dock app is macOS-only and best-effort: pywebview pulls the heavy pyobjc wheels.
# Failure must NOT abort the bootstrap (the web UI still works in a browser).
if [ "$(uname)" = "Darwin" ]; then
  echo "==> installing Dock app extra (best-effort)"
  if .venv/bin/python -m pip install -e ".[app]"; then
    echo "    Dock app deps ready"
  else
    echo "    WARNING: pywebview (Dock app) failed to install — continuing without it." >&2
    echo "             Install later with: .venv/bin/python -m pip install -e \".[app]\"" >&2
  fi
fi

echo "==> configuring this machine"
exec .venv/bin/python -m internshelper.setup --repo-dir "$REPO_DIR" "$@"
