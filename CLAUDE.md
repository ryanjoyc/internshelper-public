# internsHELPer

This repo ships its own onboarding skills. Use them rather than re-deriving the layout from scratch.

- **Where are we right now?** Read **`STATE.md`** (repo root) or invoke the **`project-state`** skill
  first — it's the living dashboard (current focus, git/VCS status, planning-doc index). Keep it
  current with **`update-project-state`**, and run **`session-state-check`** before wrapping up.
- **Starting work here?** Invoke the **`internshelper-guide`** skill first — it's the orientation
  map (module layout, data flow, the four CLIs, config/data, conventions). It points you to the
  right source file instead of scanning the whole tree.
- **Made a structural change?** Before finishing, invoke the **`update-internshelper-guide`** skill
  if you added/renamed a module, connector, or CLI subcommand, or changed the data flow, schema, or
  config — so the guide stays accurate for the next agent.
- **Task-specific skills:** `add-source` (add a job board from messy input),
  `review-internships` (classify the pending queue match/no_match), and `classify-terms`
  (grade postings by term — "is this Summer 2027?" — upgrading the heuristic's
  POSSIBLE/UNREADABLE rows; free, runs in your interactive session).

The graded **term classifier** lives in `internshelper/term.py` (Tier-0 heuristic +
`classify`/`list-candidates`/`set-term`/`summary` CLI) with description enrichment in
`internshelper/enrich.py` (ATS-JSON fetchers). Target term is configurable in
`[term]` (`settings.toml`). In-app LLM (`term classify --llm`) is **metered/opt-in** —
the free path is `/classify-terms` in an interactive Claude Code session.

Always run Python via the project venv: `.venv/bin/python`.
