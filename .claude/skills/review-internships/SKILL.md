---
name: review-internships
description: Audit the internsHELPer Inbox tiers. Use when the user runs /review-internships, asks to "review the new postings", "go through the internship inbox", "clean up the board tiers", or after an internsHELPer Apply-first digest email. Reads each inbox posting's raw payload and curates the tiers via the internshelper.review CLI — pin confirmed strong fits to Apply first, dismiss confirmed junk — then reports the Apply-first shortlist.
---

# Audit the internsHELPer Inbox

There is no approval gate: every collected posting is already on the Board, grouped into
apply-order tiers (`apply_first` = dream companies, `target` = the approved index,
`everything_else`, `long_shots` = low fit-score). You are the **tier auditor**: confirm
the machine's sort against the actual payloads and correct it — you never gate.

Run everything from the repo root with the project venv: `.venv/bin/python`.

## Judgment — be GENEROUS, curation over gating

Judge fit against **`config/profile.md`** (the user's role-fit profile), JD-over-title — title
keywords must never be the reason to dismiss. the user would rather skim a loosely-relevant
role himself than have it hidden.

- **Dismiss** ONLY confirmed junk: clearly non-technical with no CS / data / quant /
  analytical angle at all (pure sales, retail/food service, non-technical ops), or
  obviously wrong level (senior/staff roles). When in any doubt, leave it alone —
  or dismiss with the reason prefixed `borderline:` so it stays a weak label and
  visible under the board's Dismissed view.
- **Pin to `apply_first`** ONLY clear standouts stuck in a lower tier: a posting whose
  JD screams profile-fit (great role + intern/new-grad level) that the company rule
  buried in `everything_else` or `long_shots`. Pins are sticky and train the ranker —
  a handful of deliberate pins beats mass promotion.
- **Everything else stays put.** The tiers + learned ordering are the default; your
  job is fixing confirmed mistakes in both directions, not re-sorting the world.

## Steps

1. **Pull the inbox (best-first):**
   ```bash
   .venv/bin/python -m internshelper.review list-inbox
   ```
   JSON rows carry `tier` (effective — a pin wins), `pinned_tier`, `rank_score`,
   `payload_path`, `posted_at`. Audit `long_shots` and `everything_else` for buried
   gems, and `apply_first`/`target` for junk that rode a good company name. Note
   `posted_at` — a very stale posting is lower-value even if relevant.
   Filter one tier at a time to keep context bounded:
   `--tier long_shots --limit 50`, etc.

2. **For each posting you're acting on**, read the payload first:
   ```bash
   cat <payload_path>          # the raw JSON the collector saved
   .venv/bin/python -m internshelper.review dismiss <posting_id> --reason "<one line>"
   .venv/bin/python -m internshelper.review pin <posting_id> --tier apply_first
   ```
   (`undo-dismiss` / `unpin` revert; every action retrains the ranker automatically.
   A posting with no `payload_path` — judge from the title alone and say so.)
   Never touch a posting the user applied to (`review applied` lists them).

3. **Report.** Show the Apply-first shortlist:
   ```bash
   .venv/bin/python -m internshelper.review list-inbox --tier apply_first
   ```
   Present title — company — location — URL, plus counts of what you dismissed and
   pinned (with one-line reasons). The Board reflects everything on reload.

## Notes
- This is free — it runs in the current Claude Code session, no API key.
- Actions are stored in SQLite (`data/internshelper.db`) and are individually
  undoable in the app; safe to stop and resume — re-running only sees the current inbox.
