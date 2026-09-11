---
name: investigate-flags
description: >-
  Investigate every posting the user flagged for review on the internsHELPer Board
  (dead links, "the website didn't show anything", suspect data) and resolve each one.
  Use when the user runs /investigate-flags, asks to check the flagged queue, or flags
  something and asks what is wrong. Opens the stored link live, retries via the ATS
  JSON API, re-enumerates the source, then resolves or diagnoses every flagged row.
---

# Investigate the flagged-for-review queue

The Board's ⚑ Flag parks a posting the user distrusts — typically "I clicked it and the page
showed nothing." A flag is deliberately **not** a dismissal: it carries no ranking label, because
bad *data* is not a preference. This skill is the investigator that clears the queue. For each
flagged posting it must answer one question with evidence: **is our data wrong, or is the posting
really gone?** — and then resolve it in the app.

**The `flag_reason` is a lead, not ground truth.** It's the user's report of a symptom, filed in
one click, possibly mistaken (a slow page, a mis-click, an SPA shell that later loads). Use it to
decide what to check *first* — "link shows nothing" → start by fetching that link — but the
verdict must rest entirely on what the investigation actually finds. Concluding the opposite of
the reason ("the link works fine") is a perfectly good outcome, and never skip a verification
step just because the stated reason seems to explain everything.

Three root causes to distinguish (this is the whole game):

1. **`working`** — the page is actually fine (SPA that needs the JSON API, transient outage,
   user mis-click). The flag was a false alarm → restore to the Inbox.
2. **`gone`** — the employer removed the posting (and the source board agrees, or the source
   still lists a stale row). Confirmed-dead → dismiss with evidence.
3. **`bad_link`** — the posting exists but **our stored URL is wrong** (connector built it badly,
   or the board changed URL schemes). This is a code bug wearing a posting costume → leave
   flagged, report the correct URL and the broken pattern.

Run everything from the repo root with the project venv: `.venv/bin/python`. Scratch files go in
the session scratchpad directory, never the repo.

## Steps

1. **Enumerate the queue**:
   ```bash
   .venv/bin/python -m internshelper.review list-flagged
   ```
   JSON rows: `{posting_id, source_key, company, title, url, payload_path, is_active,
   flag_reason, flagged_at}`. Empty → tell the user the flagged queue is clear and stop.
   Tell the user how many you're investigating and from which sources.

2. **Guard already-applied jobs (default ON).** `.venv/bin/python -m internshelper.review applied`
   — remove every returned `posting_id` from the set (never unflag/dismiss an application;
   list any excluded back to the user). Only include applied ids if the user explicitly said so
   this run.

3. **Re-enumerate each involved source once** (not per posting):
   ```bash
   .venv/bin/python -m internshelper.sources test "<source_key>" --json
   ```
   For each flagged posting, note whether its `posting_id` is still enumerated and what URL the
   connector derives for it **today**. Save per-source results to the scratchpad. This
   comparison — stored URL vs freshly-derived URL vs "not listed anymore" — is what separates
   `bad_link` from `gone`. If `sources test` itself errors, record that: a whole-source fetch
   failure is a finding, not a blocker.

4. **Fan out one investigation agent per batch** (~5 postings each — fewer than deep-scan,
   because each investigation digs deeper). Write per-batch JSON files to the scratchpad where
   each item carries: the flagged row, the user's `flag_reason`, plus your step-3 findings
   (`still_listed`, `current_url`). Dispatch the agents in a single message (parallel),
   `run_in_background: false`, `subagent_type: general-purpose`, using the prompt below.

5. **Resolve in the app** (all reversible; act by `posting_id`):
   - `working` → `.venv/bin/python -m internshelper.review unflag <id>` — back to the Inbox.
   - `gone` → `.venv/bin/python -m internshelper.review dismiss <id> --reason
     "flag-investigation <YYYY-MM-DD>: confirmed gone; <evidence>"` — a confirmed-dead posting
     is a legitimate dismissal (this also clears nothing else: the flag columns stay, which is
     fine — dismissed wins for visibility, and an undo-dismiss would surface the flag again).
   - `bad_link` / `blocked` → **leave flagged** (no CLI action). These need a human or a code fix;
     the flag is doing its job as a parking brake.

6. **Diagnose patterns.** Group `bad_link` and `blocked` results by `source_key`. If every
   flagged posting from one source shows the same failure shape (e.g. all Workday CXS
   `externalPath` URLs 404 while the CXS API still returns the jobs), name the connector file
   (`internshelper/connectors/<type>.py`), state the suspected bug and the evidence (stored URL
   vs working URL, side by side), and **offer** to fix the connector — do NOT change connector
   code inside this skill run without the user's go-ahead.

7. **Report.** One table: company — title — verdict (working/gone/bad_link/blocked) — action
   taken (unflagged / dismissed / left flagged) — one-line evidence. Then counts, the
   connector-bug diagnosis section (if any), and a reminder that the Board's Flagged view now
   reflects the resolutions (restored cards are back in their company groups; confirmed-gone under
   Dismissed).

## The investigation-agent prompt (bake this in; substitute the batch path)

```
Read the JSON file at <SCRATCHPAD>/flag_batch_<N>.json — a list of flagged postings
{posting_id, company, title, url, flag_reason, still_listed, current_url}. The user flagged each
one as suspect; flag_reason is THEIR one-click report of a symptom (usually "the link showed
nothing"). Treat flag_reason as a HYPOTHESIS to test first, never as an established fact — the
user may have hit a slow load, an SPA shell, or simply been mistaken. Check what it describes
first, then verify independently either way; your verdict must rest only on what YOU observe,
and contradicting the flag_reason is a valid, common outcome. For EACH posting, determine with
evidence which of these is true: working | gone | bad_link | blocked.

Per posting:
1. WebFetch the stored `url`. A blank/near-empty page is NOT proof of anything — Ashby / Lever /
   Greenhouse / Workday SPAs often render empty HTML. Retry via the public JSON API before
   concluding: Lever v0/postings, Greenhouse board API, Ashby posting-api, Workday CXS
   (POST …/wday/cxs/<tenant>/<site>/jobs, or the job detail under the CXS path). CAVEAT: on
   Workday CXS never trust the `startDate` field — it is the POSTING date, not the internship
   start.
2. If `current_url` differs from `url`, WebFetch `current_url` too.
3. Decide:
   - working  = you retrieved the actual job content (via page or API) at the STORED url, and it
                matches this title/company. The flag was a false alarm — say what the user
                probably hit (SPA shell, transient error).
   - gone     = the job is not retrievable anywhere AND still_listed is false, OR the page/API
                positively says closed/removed.
   - bad_link = the job EXISTS (still_listed true, and/or you found it at current_url or via the
                API) but the STORED url does not reach it. Include the URL that DOES work — this
                is connector-bug evidence.
   - blocked  = you could not verify either way (bot wall, auth wall, network refusals on every
                route). Say exactly what you tried.

Output EXACTLY one pipe-delimited line per posting, NO other prose:
POSTING_ID | working|gone|bad_link|blocked | <working URL or "-"> | high|med|low | <short evidence: quote/status code/what the API returned>
```

## Notes

- This runs in the current Claude Code session and needs no separate internsHELPer API key.
- Division of labor: `/review-internships` judges *fit* from payloads; `/deep-scan-source`
  verifies a *term* across one whole source; **this** clears the *flagged* queue by verifying
  the postings' existence and our data quality — per-posting, deepest of the three.
- The flag itself never trains the ranker; only the `gone → dismiss` resolution creates a
  (legitimate) negative label. `unflag` restores the card to its configured company group.
- Everything written here is reversible in the app: Restore on the Flagged view, undo-dismiss
  on the Dismissed view.
