---
name: internshelper-engineering
description: Apply pstack-codex engineering rigor to substantial internsHELPer investigations, bug fixes, features, refactors, UI hardening, or implementation plans. Use when engineering-mode is invoked in this repository or when a task needs coordinated implementation and verification. Do not replace task-specific collection or review skills.
---

# internsHELPer engineering adapter

Use `$engineering-mode` as the general workflow, then apply these repository-specific rules.

## Orient

1. Read `STATE.md` through `$project-state`.
2. Read `$internshelper-guide` before locating or changing code.
3. Read `docs/README.md` and only the active reference needed for the task.
4. Check `.code-review-graph/` first for code work. Run `code-review-graph status`; when the graph matches the branch, use its available change-impact query to narrow the affected files and symbols. If the graph is stale or the installed CLI exposes no suitable query, report that limitation, then narrow with the actual diff, `rg`, callers, tests, and direct code inspection.
5. Snapshot `git status` and preserve all pre-existing work. Do not fold unrelated changes into the task.

## Route

- Source registration uses `$add-source`.
- Company-board research uses `$resolve-companies`.
- Inbox auditing uses `$review-internships`.
- Exhaustive per-link term verification uses `$deep-scan-source`.
- Flagged posting investigation uses `$investigate-flags`.

Those workflows keep their approval and mutation boundaries. Engineering mode does not broaden them.

## Implement and prove

- Run Python only through `.venv/bin/python`.
- Keep data and review logic outside web routes. Routes should translate HTTP requests into domain operations.
- Preserve company-first grouping, user-controlled curation, reversible review actions, and the separation between company grouping and source approval.
- For UI changes, verify relevant desktop breakpoints, keyboard and focus behavior, HTMX replacement behavior, error feedback, and the optional Playwright matrix when warranted.
- For persistence changes, verify schema compatibility, reversibility, and the stored state after the operation.
- Prefer the narrow test first, then run the standard suite when shared behavior changes. Use the browser marker separately when UI runtime behavior changes.

## Divide review responsibility by risk

This is a local, single-user application. Do not make the user review every implementation detail.
The engineering agent owns the code-level audit, affected-callers review, regression tests, and
runtime proof. the user owns the product experience and decisions whose meaning depends on his intent.

Always surface for the user's review:

- visible UI/workflow changes and the short acceptance path that exercises them;
- changes to what a user action means, especially curation, grouping, merge, dismiss, and Undo;
- database schema/migrations, persistent-data reinterpretation, deletion, or reduced reversibility;
- config writes, source changes, background collection/email behavior, dependency/runtime changes,
  Dock-app packaging, and any external side effect or new authority requirement.

If none of those changed, say so explicitly. Internal SQL mechanics, route wiring, focus plumbing,
CSS details, pagination calculations, refactors, and individual test implementation stay delegated
unless they expose a product tradeoff or unresolved risk.

For a substantial handoff, lead with a compact review packet:

1. what the user will notice;
2. what changed in persistent data semantics (including "no schema change" when true);
3. product decisions made and anything intentionally unchanged;
4. external or irreversible effects;
5. a five-to-ten-minute acceptance tour;
6. executable verification evidence and remaining risks;
7. suggested commit boundaries, separating unrelated user-owned work.

When acceptance clicks could affect real data or config, prefer offering a review instance backed
by copied database/config files. Do not substitute agent test results for the user's experience review,
and do not send implementation details back to him unless they help evaluate one of the risks above.

## Finish

1. Use `$update-internshelper-guide` after a structural change.
2. Use `$update-project-state` after meaningful work.
3. Use `$session-state-check` before wrapping up a work session.
4. Report uncommitted and unpushed work. Never commit or push unless the user asks.
