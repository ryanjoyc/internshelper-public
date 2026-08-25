# internsHELPer documentation

This directory contains current references only. Completed and superseded implementation plans are
kept in Git history, not beside active guidance.

## Start here

1. Read [`../STATE.md`](../STATE.md) for current focus, runtime caveats, branch status, and the
   authoritative documentation index.
2. Use the repository's `internshelper-guide` skill for the module map, data flow, CLIs, config,
   and testing conventions.
3. Read [`../README.md`](../README.md) for setup and operator workflows.
4. Read [`../ROADMAP.md`](../ROADMAP.md) only for forward-looking product priorities.

## Current references

| Document | Use it for |
|---|---|
| [`source-coverage.md`](source-coverage.md) | Supported connectors, configured footprint, known boundaries, and connector acceptance criteria. |
| [`../config/profile.md`](../config/profile.md) | the user's broad role-fit guidance for agent-assisted posting audits. |

## Documentation rules

- `STATE.md` is a lean dashboard, never a changelog.
- `ROADMAP.md` contains active direction and deferred decisions, not completed task checklists.
- Structural code changes must update both repository-local `internshelper-guide` copies:
  `.agents/skills/internshelper-guide/SKILL.md` and
  `.claude/skills/internshelper-guide/SKILL.md`.
- New planning documents belong here only while they guide unfinished work. Once executed or
  superseded, remove them and update every inbound pointer; Git history is the archive.
- Do not record live scan counts or timestamps here. Use the Health page and SQLite run history.
