---
name: resolve-companies
description: Research each PENDING company in the internsHELPer approved-companies index and turn it into a board PROPOSAL for the user's approval. Use when the user runs /resolve-companies, says "resolve my companies", "find boards for my company list", "run the company resolver", or right after adding companies with `companies add`. Finds each company's real careers board on any supported ATS (Greenhouse/Lever/Ashby/Workday), fetch-tests it, and records it via `companies propose` — it never approves; Approval A belongs to the user on the web Companies page.
---

# Resolve pending companies into board proposals

The approved-companies index (`config/companies.yaml`, or `INTERNSHELPER_COMPANIES`) is the user's
private master list. This skill does
the fallible research step — company name → its real careers board — and parks each finding as
a **proposal** for the user's yes/no decision (Approval A). Scanning stays deterministic; nothing
is scanned until the user approves.

Run everything from the repo root with the project venv: `.venv/bin/python`.

## Steps

1. **Pull the pending list:**
   ```bash
   .venv/bin/python -m internshelper.companies list --json
   ```
   Work the `status: "pending"` entries (skip ones that already carry a `proposal` unless the
   user asks to redo them). Skip `no-board` companies unless the user explicitly asks to
   re-check. Nothing pending → say so and stop.

2. **Research each company's board** — subagent fan-out is fine (~4 companies per
   general-purpose agent, dispatched in parallel). Reuse the `/add-source` recipe:
   - WebSearch `"<company> greenhouse OR lever OR ashby OR workday careers"`, or WebFetch the
     careers page and look at apply-link hosts (`boards.greenhouse.io/<token>`,
     `jobs.lever.co/<token>`, `jobs.ashbyhq.com/<org>`, `<tenant>.wd<N>.myworkdayjobs.com/<Site>`).
   - An embedded board on a careers page: the sniffer inside `sources add`/`detect` handles it,
     but for research you can also just read the HTML for those hosts.
   - **Validate before proposing** — fetch-test the candidate:
     ```bash
     .venv/bin/python -m internshelper.sources test "<board-url>" --json
     ```
     Note the `count` and any `diagnostics`. A Workday tenant that errors with the >2000-cap
     message is still a VALID find (approve adds `search: intern` automatically) — record the
     total from the error message as the count evidence.

3. **Record each finding as a proposal:**
   ```bash
   .venv/bin/python -m internshelper.companies propose "<Name>" \
       --url "<board-url>" --count <n> --evidence "<how it was found, one line>"
   ```
   Nothing public found → tell the user what was checked; only run
   `companies mark-no-board "<Name>" --notes "<what was checked> — <date>"` with their OK, or
   when the evidence is conclusive (e.g. careers page runs on an unsupported ATS — name it).

4. **Never `approve` from this skill.** Approval A belongs to the user — on the web **Companies**
   page (cards with Approve/Reject) or `companies approve "<Name>"`. Finish by reporting how
   many proposals await and pointing him at `/companies` in the app.

5. **The convenience chain (on request, after the user approves):** run one collect cycle
   (`INTERNSHELPER_FEATURE_EMAIL=false .venv/bin/python -m internshelper.run`), then offer
   `/deep-scan-source` on the newly resolved boards (term + profile fit), and report the
   shortlist.

## Notes

- Companies already registered as sources: `companies propose` + approve just **links** them
  (dedupe) — no duplicate source is created.
- The proposal's `count`/`evidence` are what the user sees on the approval card — make the evidence
  specific ("careers page apply links point at jobs.ashbyhq.com/ramp", not "found it").
- Related: `/add-source` (add one board directly, no index), the `companies` CLI
  (`internshelper/companies.py`), the web Companies page (Approval A UI).
