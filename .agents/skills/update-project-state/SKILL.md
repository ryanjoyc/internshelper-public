---
name: update-project-state
description: Keep STATE.md (the project dashboard) current — and lean. Use after a meaningful change to this repo (finished/started a piece of work, a structural change, a commit/branch/push, a new or removed planning doc), when the state-reminder hook fires, or when the user says "update the state", "refresh STATE.md", "the project state is stale". Refreshes current focus, re-snapshots git, re-indexes planning docs, and PRUNES stale entries.
---

# Update the project state

`STATE.md` (repo root) is only useful if it's trustworthy and **lean**. Your job is to make it match
reality again and then *shrink* it — not to log everything that happened. Sibling skills:
`project-state` (reads it), `session-state-check` (end-of-session sweep).

## When this applies

Refresh STATE.md when something changed the answer to "where are we right now":

- A piece of work **started or finished**, or the immediate next step changed.
- A **structural change** (module/feature/architecture) — also consider the repo's own guide skill.
- **Git state moved**: you committed, branched, pushed, merged, or the working tree got dirty/clean.
- A **planning doc** was added, renamed, finished, or deleted.

**Does NOT apply** to routine edits that don't move the situation — keep STATE.md still. If it would
read identically afterward, leave it alone. STATE.md must not churn on every commit.

## Procedure

1. **Read `STATE.md`.**
2. **Refresh Current focus.** Update the active item + next step to what's true *now*. **Delete**
   anything finished or any resolved blocker — do not move it to a history list (there is none).
   Cap at ≤5 bullets; if you have more, you're tracking too much.
3. **Re-snapshot Git / VCS status** with read-only commands (never mutate):
   - `git status --porcelain` → uncommitted/staged summary (or "clean").
   - `git branch -vv` and `git rev-list --left-right --count @{u}...HEAD` → branch + ahead/behind.
   - `git log --oneline @{u}..HEAD` → unpushed commits (or "none").
   - `git branch -a` → flag stale/unmerged branches worth noting.
   - **No upstream/remote, or no commits yet** (`@{u}` errors either way)? Write
     "no upstream configured — ahead/behind N/A" and "unpushed: none"; never surface the raw `fatal:`.
   - **Not a git repo?** Write "not a git repository" and move on — never error.
   This section is **regenerated**, never appended. Update the `Last updated` / snapshot date.
4. **Re-index Planning docs.** Scan for planning docs (ROADMAP, specs, design docs, anything under a
   `docs/`/`plans/` dir). Add new ones as a single row (path + one-line summary + status); drop rows
   for deleted docs; fix renamed paths. Pointers only — never paste their contents.
5. **Bump `Last updated`** to today.

## Self-check before finishing (the leanness gate)

- STATE.md still fits on **one screen** (~80 lines). If not, you over-tracked — prune.
- Current focus has **no completed items** lingering and **no change-log creeping in**.
- The Git section is a fresh snapshot, not an accumulation.
- The Planning-doc index is pointers + one-liners — no doc content pasted in.
- Every planning doc listed **still exists** at its path.

If a detail feels important but doesn't fit the dashboard, it belongs in a planning doc or in a git
commit message — not in STATE.md.
