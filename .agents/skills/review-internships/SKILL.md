---
name: review-internships
description: Audit internsHELPer Inbox postings. Use when the user runs /review-internships, asks to "review the new postings", "go through the internship inbox", or after a Top-target digest. Reads raw payloads and dismisses only confirmed junk; company browsing groups remain user-controlled.
---

# Audit the internsHELPer Inbox

There is no approval gate: every collected posting is already on the Board, grouped by company
into Top targets, Known companies, Worth discovering, or neutral Unclassified. You are a
generous posting auditor. Never change a company group based on one role.

Run everything from the repo root with the project venv: `.venv/bin/python`.

## Judgment — be GENEROUS, curation over gating

Judge fit against the user's private role-fit profile: `INTERNSHELPER_PROFILE` when set, otherwise
**`config/profile.md`**. If it is missing, stop and ask the user to customize
`config/profile.example.md`; do not infer personal criteria. Judge JD-over-title — title
keywords must never be the reason to dismiss. The user would rather skim a loosely relevant
role himself than have it hidden.

- **Dismiss** ONLY confirmed junk: clearly non-technical with no CS / data / quant /
  analytical angle at all (pure sales, retail/food service, non-technical ops), or
  obviously wrong level (senior/staff roles). When in any doubt, leave it alone —
  or dismiss with the reason prefixed `borderline:` so it stays a weak label and
  visible under the board's Dismissed view.
- **Everything else stays put.** Rank score is only a hint and company groups are not role-fit
  judgments. Do not pin individual postings or reclassify companies during this audit.

## Steps

1. **Pull the inbox:**
   ```bash
   .venv/bin/python -m internshelper.review list-inbox
   ```
   JSON rows carry company group in `tier`, plus `rank_score`, `payload_path`, and
   `posted_at`. Audit all groups by actual JD. A company name is never enough to dismiss.

2. **For each posting you're acting on**, read the payload first:
   ```bash
   cat <payload_path>          # the raw JSON the collector saved
   .venv/bin/python -m internshelper.review dismiss <posting_id> --reason "<one line>"
   ```
   (`undo-dismiss` reverts; every dismissal retrains the ranker automatically.
   A posting with no `payload_path` — judge from the title alone and say so.)
   Never touch a posting the user applied to (`review applied` lists them).

3. **Report.** Present the useful postings by company with title — location — URL, plus what
   you dismissed and why. Do not claim the shortlist changed company priority.

## Notes
- This is free — it runs in the current Codex session, no API key.
- Actions are stored in SQLite (`data/internshelper.db`) and are individually
  undoable in the app; safe to stop and resume — re-running only sees the current inbox.
