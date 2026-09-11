#!/usr/bin/env bash
# project-state bundle — gated Stop-hook nudge.
#
# Fires when an agent finishes responding. Stays SILENT unless the git tree is dirty or the branch
# has unpushed commits, then emits one short reminder to run the session-state-check skill before
# wrapping up.
#
# Non-blocking by design (always exits 0): it informs, it never forces the model to continue.

hook_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 0
repo_dir="$(cd "$hook_dir/../.." && pwd)" || exit 0
cd "$repo_dir" 2>/dev/null || exit 0

# Not a git repo → nothing to nag about.
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

dirty="$(git status --porcelain 2>/dev/null)"
ahead="$(git rev-list --count @{u}..HEAD 2>/dev/null || echo 0)"

# Clean tree and nothing unpushed → stay quiet.
if [ -z "$dirty" ] && [ "$ahead" = "0" ]; then
  exit 0
fi

reason="project-state: this repo has uncommitted or unpushed work. Consider running the session-state-check skill before wrapping up (it verifies STATE.md + planning docs are current and flags loose git state)."

# Emit a non-blocking notice. systemMessage surfaces a warning to the user without forcing the
# model to keep going; the stderr line is a harmless fallback for setups that don't render it.
printf '{"systemMessage":%s}\n' "$(printf '%s' "$reason" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null || printf '"%s"' "$reason")"
printf '%s\n' "$reason" >&2
exit 0
