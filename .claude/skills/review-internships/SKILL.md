---
name: review-internships
description: Classify the internsHELPer pending-posting queue. Use when the user runs /review-internships, asks to "review the new postings", "go through the internship queue", "classify what the collector found", or after an internsHELPer review-nudge email. Reads each pending posting's raw payload and records a match/no_match verdict via the internshelper.review CLI, then prints the confirmed shortlist.
---

# Review internsHELPer pending postings

The hourly collector scrapes the configured boards and stores every new posting as
`pending` with **keyword priority hints** — it does NOT classify. You are the classifier.
Your job: read each pending posting and record a verdict. Keyword hints only order the
queue; **scan everything**, the user does not trust keyword precision.

Run everything from the repo root with the project venv: `.venv/bin/python`.

## What counts as a match — be GENEROUS

**Default to `match`. Cast a wide net.** the user would rather see a loosely-relevant role and skip
it himself than have it filtered out — applying to an extra place costs nothing. Generosity over
precision. **Do NOT screen on required years of experience or strictness.**

`match` = anything plausibly relevant to a CS-degree student / new grad — and "somewhat related"
counts: software/SWE, data (analyst / engineer / scientist), ML/AI, quant (developer **and**
research), security, infra/platform, embedded, IT / digital technology, and technical or
analytical analyst / rotational / "all-tracks" programs (e.g. BlackRock-style). Internships,
co-ops, **and** new-grad/entry roles all qualify.

`no_match` = ONLY when the role is clearly non-technical with no CS / data / analytical angle at
all — pure sales, retail/food service, or non-technical operations/management. When in any doubt
→ `match`, and prefix the reason with "borderline:" so the user can eyeball it.

## Steps

1. **Pull the queue (candidates first, newest first):**
   ```bash
   .venv/bin/python -m internshelper.review list-pending
   ```
   This returns JSON ordered keyword-candidates first, then by `posted_at` (the source's own
   posted/added date) newest-first. Process in that order — recent roles matter most — but
   **review every item**, candidates AND the rest. Note each row's `posted_at` when judging:
   a very stale posting is lower-value even if relevant.

2. **For each posting**, read the title and the full payload, then record a verdict:
   ```bash
   cat <payload_path>          # the raw JSON the collector saved
   .venv/bin/python -m internshelper.review set-verdict <posting_id> \
       --verdict match|no_match --reason "<one line>"
   ```
   Work in chunks of ~25–50 per pass to keep context bounded. After a chunk, re-run
   `list-pending` and continue until it returns an empty list. (A posting with no
   `payload_path` — e.g. older rows — judge from the title alone and say so in the reason.)

3. **Finish** — reset the notify flag so the next batch can nudge again:
   ```bash
   .venv/bin/python -m internshelper.review finish
   ```

4. **Show the user the shortlist** — the confirmed matches:
   ```bash
   .venv/bin/python -m internshelper.review summary
   ```
   Present them as a short list (title — company — location — apply URL), with counts
   (reviewed N, matched M). The Streamlit dashboard also reflects the verdicts.

## Notes
- This is free — it runs in the current Claude Code session, no API key, no per-posting charge.
- Verdicts are stored in SQLite (`data/internshelper.db`); re-running the skill only sees
  what is still `pending`, so it is safe to stop and resume.
