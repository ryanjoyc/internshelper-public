---
name: project-state
description: The "where are we right now" dashboard for this repo. Use FIRST when starting work here, resuming after a break, or when the user asks "what's the state of this project", "where did we leave off", "what am I working on", "what's the git status / is anything uncommitted", or "what planning docs exist". Reads STATE.md — the project's living status (current focus, git/VCS status, planning-doc index).
---

# Project state

`STATE.md` (repo root) is this project's **living dashboard** — the fast answer to *"where are we
right now?"* It has exactly three sections: **Current focus**, **Git / VCS status**, and a
**Planning-doc index**. Read it at the start of work so you act on the current situation instead of
re-deriving it.

It is deliberately **one screen**. It is a dashboard, not a log — history lives in git, and deep
plans live in the planning docs it indexes (e.g. ROADMAP, specs). This skill is its sibling to
`update-project-state` (keeps it current) and `session-state-check` (end-of-session sweep).

## How to use it

1. **Read `STATE.md`** at the repo root. Lead with **Current focus** — that's what to work on.
2. **Trust the git snapshot only as a hint.** It's a point-in-time snapshot with a `Last updated`
   date. If you're about to make decisions that hinge on git state (committing, branching,
   "is anything uncommitted?"), re-run the actual read-only git commands rather than trusting the
   line — then invoke `update-project-state` to refresh the snapshot.
3. **Follow the Planning-doc index** to the right deep doc (ROADMAP, a spec) when you need detail.
   STATE.md only points; it never duplicates their content.

## If STATE.md doesn't exist

This repo hasn't been set up for state tracking. Don't hand-roll one — invoke the global
**`init-project-state`** skill, which scaffolds STATE.md plus the `update-project-state` /
`session-state-check` skills and the reminder hook, asking you what it needs first.

## The leanness rule (why this stays useful)

A dashboard you can't read at a glance can't drive decisions. If STATE.md has grown past ~one
screen, or a section has turned into a running history, it's tracking too much — **prune it**: drop
finished focus items, push specifics down into the relevant planning doc or git, and keep only what
answers "where are we now." When you notice this, fix it via `update-project-state`.
