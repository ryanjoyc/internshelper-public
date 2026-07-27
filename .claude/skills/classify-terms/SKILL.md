---
name: classify-terms
description: Upgrade the internsHELPer term classifier's ambiguous postings (POSSIBLE/UNREADABLE) into graded Summer-2027-style verdicts. Use when the user runs /classify-terms, asks to "figure out which postings are Summer 2027", "grade the undated internships", "judge the term of the maybes", or after a `term classify` run leaves POSSIBLE/UNREADABLE rows. Reads each candidate's payload and records a graded term verdict via the internshelper.term CLI. This is the FREE Tier-1 lane — it runs in your interactive Claude Code session (subscription), not a metered API call.
---

# Classify internsHELPer postings by term (e.g. Summer 2027)

The `term classify` pass runs a free **deterministic** heuristic over every posting: it
resolves the clear cases (explicit *Summer 2027* → `EXPLICIT`; an explicit 2026 / fall term →
`NOT`) and leaves a gray-zone remainder as `POSSIBLE` (an internship with no stated term) or
`UNREADABLE` (no description we could fetch). **You are the upgrade**: read each candidate and
record a graded verdict the heuristic couldn't reach.

Run everything from the repo root with the project venv: `.venv/bin/python`.

## Why this is free
This runs in your **interactive** Claude Code session, on your subscription — no API key, no
per-token charge. The in-app `term classify --llm` path is the *opposite*: it calls a model
itself and **bills per token** (headless/SDK usage is metered, not subscription). Prefer this
skill; only reach for `--llm` if you explicitly want the app to do it unattended and accept
the cost.

## The graded rubric (match the heuristic's vocabulary)
Decide against the target term (default **Summer 2027**; confirm with `term summary`):
- `EXPLICIT` — text literally says the target (Summer 2027 / 2027 summer / a start date in ~May–Aug 2027).
- `LIKELY` — strong implicit cue pointing to the target cohort (e.g. a 2027 start, grad window that only fits a 2027 summer).
- `POSSIBLE` — internship, **no year stated** anywhere readable — can't confirm, can't exclude. Leave it here if you genuinely can't tell.
- `NOT` — an explicit different term (2026/2025, fall/spring/co-op range), or a new-grad/full-time role.
- `UNREADABLE` — you truly could not read a description (payload empty, page JS-only). Honest non-answer.

## Steps

1. **Confirm the target + see the queue size:**
   ```bash
   .venv/bin/python -m internshelper.term summary
   .venv/bin/python -m internshelper.term list-candidates
   ```
   `list-candidates` returns the POSSIBLE/UNREADABLE rows as JSON, freshest first, each with
   its `payload_path`, `title`, `url`, and current heuristic `term_evidence`.

2. **For each candidate**, read the saved payload (the JD the enrich step fetched), then record
   a graded verdict:
   ```bash
   cat <payload_path>          # raw JSON the collector/enrich saved (may be absent → judge from title)
   .venv/bin/python -m internshelper.term set-term <posting_id> \
       --verdict EXPLICIT|LIKELY|POSSIBLE|NOT|UNREADABLE \
       --season summer --year 2027 \
       --evidence "<short quote or reason>" --source agent
   ```
   Quote the actual phrase that decided it in `--evidence`. Omit `--season/--year` (or pass
   `--season none`) when the term is unknown. Work in chunks of ~25–50, re-running
   `list-candidates` until it returns an empty list.

3. **Show the shortlist** — the confirmed + likely target-term roles:
   ```bash
   .venv/bin/python -m internshelper.term summary
   ```
   Present EXPLICIT/LIKELY as a short list (title — company — location — apply URL) with the
   verdict counts. The Streamlit dashboard's term filter reflects these too.

## Notes
- Verdicts persist in SQLite (`data/internshelper.db`); re-running only sees what's still
  POSSIBLE/UNREADABLE, so it's safe to stop and resume.
- Don't over-claim: leaving a row `POSSIBLE` is a valid, honest outcome. The requester asked
  for a graded answer, not a forced yes/no.
- The heuristic already handled the clear cases — focus your reads on the genuinely ambiguous.
