---
name: deep-scan-source
description: Exhaustively verify EVERY posting on one internsHELPer source against a specific term (e.g. "Summer 2027") by fetching each live link with a subagent. Use when the user runs /deep-scan-source, or asks to verify every posting/link on one source for a term. Enumerates the source, investigates every link, and sorts each into match / uncertain / no_match with on-page evidence. For registered sources it dismisses confirmed no-matches; matches stay in their company group.
---

# Deep-scan a source for a term

`/review-internships` audits Inbox postings **generously** for CS-relevance, judging from the
saved payload. This skill is the opposite tool: given **one source** and **one or more terms**
(e.g. "Summer 2027"), it opens **every posting's live link** and confirms the term from the actual
page — so the user can trust that no matching role was missed and no stale/mislabeled one slipped in.

It is deliberately subagent- and token-heavy: one investigation per link, no title-only guesses.

Run everything from the repo root with the project venv: `.venv/bin/python`. Put scratch files in the
session scratchpad directory, not the repo.

## Inputs (get these first)

- **Source** — a raw board URL (e.g. a GitHub README) **or** a registered `source_key`
  (`markdown:https://…`). `sources list` shows registered ones.
- **Term(s)** — what to verify, e.g. `Summer 2027`. Multiple terms → a posting **matches if it
  satisfies ANY** of them (say so back to the user). If the user hasn't given a term, ask for one —
  this skill is meaningless without it.

## Steps

1. **Enumerate every posting** (deterministic — reuses the connector):
   ```bash
   .venv/bin/python -m internshelper.sources test "<url-or-source_key>" --json
   ```
   This prints `{source_key, count, postings:[{posting_id, company, title, url, ...}], diagnostics}`.
   Note the `count`, surface any `diagnostics` (a degenerate parse means rows may be missing — say so),
   and tell the user how many links you're about to open and the rough agent count (~7 links/agent).
   Then proceed over the **whole source** — do not sample or cap.

2. **Guard already-applied jobs (default ON).** Fetch the user's applications:
   ```bash
   .venv/bin/python -m internshelper.review applied
   ```
   **Remove every returned `posting_id` from the set you're about to scan** — do NOT investigate or
   write verdicts for a job the user has applied to, so a re-scan can never demote or overwrite it.
   List the excluded applied jobs back to the user ("skipping N already-applied: …, left untouched").
   **Only** include applied ids if the user **explicitly** said so this run (e.g. "scan everything,
   even the ones I applied to" / "include applied"). When in doubt, exclude — this guard protects
   the user's real applications.

3. **Batch the postings** into groups of ~7. Write each batch to its own JSON file in the scratchpad
   (`amb_batch_0.json`, …) so each agent can `Read` its slice instead of you inlining 60+ rows.

4. **Fan out one investigation agent per batch** — dispatch them **in a single message** (parallel),
   `run_in_background: false`, `subagent_type: general-purpose`. Use the prompt template below,
   substituting the real term(s) and batch file path. Each agent opens **every** link in its batch.

5. **Aggregate** the pipe-delimited lines from all agents into three buckets and present a table:
   - **MATCH** — on-page evidence of the term(s) AND profile-fit (`fit`, or `unsure` — flag
     `unsure` rows in the report for the user's eye).
   - **UNCERTAIN** — page unreachable / undated / ambiguous term (nothing dropped silently; the
     user eyeballs these).
   - **NO-MATCH** — positively a different term, OR term-matched but `nofit` (a Summer-2027 role
     that is clearly non-technical — say which of the two reasons applies).
   Each row: company — title — verdict — open/closed — fit — one-line evidence. Give counts.

6. **Write posting actions — only if the source is registered** (`source_key` appears in `sources list`):
   ```bash
   .venv/bin/python -m internshelper.review dismiss <posting_id> \
       --reason "<term> — deep-scan <YYYY-MM-DD> (<open|closed>); <evidence>"
   ```
   Mapping: MATCH → leave visible in its configured company group; NO-MATCH → `dismiss`
   (term-matched-but-no-fit rows get reason `"<term> but not profile-fit; <evidence>"`);
   **UNCERTAIN → leave visible** for the user to inspect. The dismiss verb acts by `posting_id`,
   so it **fails loudly for rows not yet collected**. If the
   source is registered but the enumerated `posting_id`s aren't in the DB yet, run one collect cycle
   first (`INTERNSHELPER_FEATURE_EMAIL=false .venv/bin/python -m internshelper.run`), then apply.
   For an **unregistered raw URL**, skip this step entirely — deliver the report only and note the
   results weren't written (there are no DB rows to attach them to).
   **Belt-and-suspenders:** the applied ids from step 2 were already dropped from the scan set, so
   they can't appear here — but never dismiss an applied `posting_id` regardless (unless the
   user opted into scanning applied jobs).

7. **Report.** Present the shortlist (MATCH first, then UNCERTAIN), note how many actions were written
   vs report-only, and — if you wrote to a registered source — remind the user the app's Board now
   reflects it on reload (matches retain their company group; dismissals are under Dismissed).

## The investigation-agent prompt (bake this in; substitute {TERMS} and the batch path)

```
Read the JSON file at <SCRATCHPAD>/amb_batch_<N>.json — a list of postings
{posting_id, company, title, url}. For EACH, use WebFetch on `url` and determine, FROM THE ACTUAL
PAGE, whether this posting is for: {TERMS}.

Read the real job-description text, not just the title. ATS tips: for Ashby/Lever/Greenhouse/Workday
the SPA HTML is often empty — use the public JSON API instead (Lever v0/postings, Greenhouse job
board API, Ashby posting-api, Workday CXS). CAVEAT: on Workday CXS do NOT trust the `startDate`
field — it is the POSTING date, not the internship start; read the JD body for the term.

Read the private role-fit profile at `$INTERNSHELPER_PROFILE` when set, otherwise
`<REPO>/config/profile.md`. If it is missing, report that the user must customize
`config/profile.example.md` and do not guess fit. For each posting judge PROFILE-FIT from the JD (generously —
oddly-named roles like "Forward Deployed Engineer" fit if the work is CS/tech/quant/finance-relevant;
pure sales/marketing/HR/recruiting/admin do not). Title keywords must never be the reason to
reject fit.

Classify each posting's TERM:
- match     = the page shows evidence it is for {TERMS} (or an explicit early/express-interest
              pipeline feeding {TERMS}).
- no_match  = the page shows it is positively a DIFFERENT term (e.g. a different season/year).
- uncertain = page unreachable/removed/404, OR undated/rolling with no term evidence, OR genuinely
              ambiguous. If the TITLE alone explicitly states {TERMS} but the page is gone, use
              match with status=closed, confidence=med.
And its FIT: fit | nofit | unsure (from the JD vs the profile; unreachable page -> unsure).

Output EXACTLY one pipe-delimited line per posting, NO other prose:
POSTING_ID | match|no_match|uncertain | <term found> | open|closed|unknown | fit|nofit|unsure | high|med|low | <short evidence: quote a phrase from the page, or why unreachable>
```

## Notes

- This runs in the current Codex session and needs no separate internsHELPer API key.
- Complements the other skills: `/add-source` *adds* a board; `/review-internships` *audits*
  Inbox postings generously for CS-relevance from payloads; **this** *verifies* ONE specified term
  strictly, per live link, across a whole source. They can disagree on purpose — a later generous
  `/review-internships` pass could restore something this skill dismissed.
- Mirrors the user's preferred pattern (fan out one subagent per ambiguous posting to confirm the term
  from the source) and the Workday-CXS `startDate` caveat — both are baked into the agent prompt above.
- The DB writes are reversible in the app (restore from the Dismissed view) — the strict
  pass never deletes postings.
