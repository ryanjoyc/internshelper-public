---
name: session-state-check
description: End-of-session sweep — verify everything done this session got tracked before wrapping up. Use when finishing a work session, when the user says "did we track everything", "wrap up", "are we good to stop", "what's left untracked", after the state-reminder hook fires, or before stepping away. Confirms STATE.md and major planning docs reflect the session, and warns about uncommitted/unpushed work. Never creates files or commits silently.
---

# Session state check

A quick safety net run at the **end of a work session**: did everything we just did actually get
captured, so the next session starts from truth? Siblings: `project-state` (reads STATE.md),
`update-project-state` (does the writing).

This skill **verifies and proposes**. It does **not** create files or run git-mutating commands on
its own — see the guardrails.

## Procedure

1. **Recall what changed this session.** From the conversation + `git status --porcelain` and
   `git diff --stat`, list the meaningful changes (work finished/started, structural changes, new or
   edited docs). Keep it to the things that move the project's state.
2. **Check STATE.md.** Does **Current focus** reflect where things actually stand, and is the **Git /
   VCS status** snapshot current? If stale, invoke **`update-project-state`** to refresh it (that
   skill also enforces leanness — don't hand-edit STATE.md into a log).
3. **Check the major planning docs.** For each doc in STATE.md's Planning-doc index that a decision
   this session touched (e.g. ROADMAP, the active spec), confirm it still matches reality. If a
   roadmap item was completed or a decision changed its premise, **flag it and offer to update** —
   ask before editing substantive planning docs.
4. **Check version control (the messy-git net).** Surface, don't fix:
   - Uncommitted / staged work still in the tree at session end.
   - Unpushed commits and ahead/behind divergence from the remote (if there's no upstream/remote or
     no commits yet, say so plainly — "no remote configured" — rather than surfacing a raw `fatal:`).
   - Stale or unmerged branches worth resolving.
   Prompt the user to commit/push where appropriate. **Never commit, push, or branch automatically.**
5. **Report** a short summary: what's tracked ✓, what you updated, and what still needs the user's
   decision (uncommitted work, a stale roadmap, a doc that should exist but doesn't).

## Guardrails (do not violate)

- **No new files or planning docs without asking.** If the session produced something that *should*
  live in a new doc (a roadmap, a spec), **alert the user and propose it** — do not create it
  silently. Wait for a yes.
- **No git mutations.** Reads only (`status`, `diff`, `log`, `branch`, `rev-list`). Committing,
  pushing, branching, merging are the user's call — prompt, don't perform.
- **Keep STATE.md lean.** Route any STATE.md writes through `update-project-state` so the one-screen
  / no-change-log / prune-stale rules are applied. This skill must not turn STATE.md into a session log.
