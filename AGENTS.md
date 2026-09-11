# internsHELPer

This repo ships its own onboarding skills. Use them rather than re-deriving the layout from scratch.

For code work, check `.code-review-graph/` first and use `.venv/bin/code-review-graph` when it is
installed, otherwise `code-review-graph`, to narrow the affected files and symbols. The graph may
be stale or built on another branch, so report that condition and verify changed code directly
before editing.

## New-instance reading order

1. **`STATE.md`** — current focus, runtime caveats, Git snapshot, and active-doc index.
2. **`internshelper-guide` skill** — module map, data flow, CLIs, config/data, and conventions.
3. **`docs/README.md`** — documentation catalog and lifecycle rules.
4. **`README.md`** for setup/operation or **`ROADMAP.md`** for future direction, as needed.

- **Where are we right now?** Read **`STATE.md`** (repo root) or invoke the **`project-state`** skill
  first — it's the living dashboard (current focus, git/VCS status, planning-doc index). Keep it
  current with **`update-project-state`**, and run **`session-state-check`** before wrapping up.
- **Starting work here?** Invoke the **`internshelper-guide`** skill first — it's the orientation
  map (module layout, data flow, the CLIs, config/data, conventions). It points you to the
  right source file instead of scanning the whole tree.
- **Made a structural change?** Before finishing, invoke the **`update-internshelper-guide`** skill
  if you added/renamed a module, connector, or CLI subcommand, or changed the data flow, schema, or
  config. Keep both `.agents/` and `.claude/` guide copies semantically synchronized.
- **Task-specific skills:** `add-source` (add a job board from messy input),
  `resolve-companies` (research pending approved-index companies into board proposals for
  the user's approval), `review-internships` (audit Inbox postings generously while leaving
  company browsing groups user-controlled), `deep-scan-source` (exhaustively verify one source against a
  specific term — e.g. Summer 2027 — plus profile fit, by opening every posting's live link),
  and `investigate-flags` (resolve the Board's ⚑ flagged-for-review queue: dead links,
  suspect data, connector-bug diagnosis).

## Codex Cloud safety

When `INTERNSHELPER_CLOUD=1`, treat the checkout as engineering-only:

- Use temporary databases and frozen fixtures. Never create `.env`, upload or open a user's real
  `data/`, or configure SMTP credentials.
- Do not run collection, email, scheduling, source/config mutation, user-state mutation,
  `live_canary`, Dock-app, or launchd commands.
- Keep agent internet access disabled. Package installation belongs to the setup phase.
- Run `bash scripts/verify-codex-cloud.sh` to check pstack, the branch-local graph, and the offline
  test surfaces.

Always run Python via the project venv: `.venv/bin/python`.
