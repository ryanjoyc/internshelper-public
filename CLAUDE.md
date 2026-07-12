# internsHELPer

This repo ships its own onboarding skills. Use them rather than re-deriving the layout from scratch.

- **Where are we right now?** Read **`STATE.md`** (repo root) or invoke the **`project-state`** skill
  first — it's the living dashboard (current focus, git/VCS status, planning-doc index). Keep it
  current with **`update-project-state`**, and run **`session-state-check`** before wrapping up.
- **Starting work here?** Invoke the **`internshelper-guide`** skill first — it's the orientation
  map (module layout, data flow, the CLIs, config/data, conventions). It points you to the
  right source file instead of scanning the whole tree.
- **Made a structural change?** Before finishing, invoke the **`update-internshelper-guide`** skill
  if you added/renamed a module, connector, or CLI subcommand, or changed the data flow, schema, or
  config — so the guide stays accurate for the next agent.
- **Task-specific skills:** `add-source` (add a job board from messy input),
  `resolve-companies` (research pending approved-index companies into board proposals for
  the user's approval), `review-internships` (classify the pending queue generously against
  `config/profile.md`), `deep-scan-source` (exhaustively verify one source against a
  specific term — e.g. Summer 2027 — plus profile fit, by opening every posting's live link),
  and `investigate-flags` (resolve the Board's ⚑ flagged-for-review queue: dead links,
  suspect data, connector-bug diagnosis).

Always run Python via the project venv: `.venv/bin/python`.
