---
name: update-internshelper-guide
description: Keep the internshelper-guide project map current. Use after a STRUCTURAL change to internsHELPer — added/removed/renamed a module or connector, added/changed a CLI subcommand, changed the data flow, DB schema, config keys, feature toggles, run/test commands, or added a skill — or when the user says "the internshelper-guide is out of date" / "update the project guide". Refreshes only the stale sections; keeps the guide mid-level.
---

# Update the internsHELPer project guide

The `internshelper-guide` skill is the repo's orientation map. It's only useful if it's
trustworthy, and it stays trustworthy only if it's refreshed when the structure changes. Your job:
detect that a change made the guide stale and patch the affected section(s) — nothing more.

The Codex guide lives at `.agents/skills/internshelper-guide/SKILL.md`; the Claude-compatible
mirror lives at `.claude/skills/internshelper-guide/SKILL.md`. Keep their technical content
synchronized while preserving platform-specific wording where useful.

## When this applies

Refresh the guide when a change altered the **map**, i.e. you (or the user) did any of:

- Added, removed, or renamed a module in `internshelper/` or a connector in
  `internshelper/connectors/`.
- Added or changed a **CLI subcommand** (in `sources`, `review`, `run`, or `setup`).
- Changed the **data flow** (the collect → store → nudge → review → dashboard chain).
- Changed the **DB schema**, **config keys** (`sources.yaml` / `settings.toml`), or
  **feature toggles** / env-var names.
- Changed the **run or test commands** (venv, pytest, dashboard, bootstrap, scheduling).
- Added, removed, or renamed a **skill** in `.agents/skills/` or `.claude/skills/`.

**Does NOT apply** to internal logic changes that don't change the map — fixing a bug inside a
function, refactoring a connector's parsing, editing copy. If the guide would read identically
after your change, leave it alone.

## Procedure

1. **Read both guide copies:** `.agents/skills/internshelper-guide/SKILL.md` and
   `.claude/skills/internshelper-guide/SKILL.md`.
2. **Locate the affected section(s)** — module map, connectors, the four CLIs, data flow, config &
   data, run & test, conventions, or related skills.
3. **Edit just those sections** to match reality. Match the existing voice and table format.
4. **Stay mid-level.** Add a one-line entry for a new module/connector/subcommand; point to the
   source file for detail. Resist turning the guide into a per-function or full-schema reference —
   that's what drifts on every commit and is explicitly out of scope.
5. **Fix cross-references** if a name changed — the `add-source` / `review-internships` /
   `update-internshelper-guide` pointers, root `AGENTS.md` / `CLAUDE.md`, and `docs/README.md`.

## Self-check before finishing

- Every module, connector, and CLI subcommand named in the guide **still exists** (`ls
  internshelper/`, `ls internshelper/connectors/`, check the argparse in the relevant module).
- Anything you **added** is now mentioned exactly once, in the right section.
- Both guide copies describe the same modules, data flow, CLIs, config, and docs entrypoints.
- The data-flow line still matches the actual chain.
- The guide is still mid-level — no section ballooned into an exhaustive reference.
