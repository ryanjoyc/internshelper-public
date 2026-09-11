---
name: add-source
description: Add a job board to internsHELPer from messy input. Use when the user runs /add-source, or says "add this board", "add <company> to internsHELPer", "track <company>'s internships", "add this careers page / job link", or pastes a job-board / careers / job-posting URL to start collecting. Resolves the input to a clean board URL, then calls the internshelper.sources CLI to validate, fetch-test, and write it.
---

# Add a source to internsHELPer

The collector scrapes the boards listed in the private `config/sources.yaml` (or the path selected
by `INTERNSHELPER_SOURCES`). Your job: turn whatever the
user gives you — a clean board URL, a company name, a careers page, a single job link, or a
GitHub list — into a **clean board URL** the deterministic CLI understands, then let the CLI
do the validating + writing. The CLI is the source of truth; you only do the research the CLI
can't (it refuses anything that needs a network lookup or guessing).

Run everything from the repo root with the project venv: `.venv/bin/python`.

## What the CLI accepts (resolve TO one of these)

- `https://boards.greenhouse.io/<token>` or `https://job-boards.greenhouse.io/<token>`
- `https://jobs.lever.co/<token>`
- `https://jobs.ashbyhq.com/<org>`
- `https://<tenant>.wd<N>.myworkdayjobs.com/<Site>` → `workday` (locale segments and a single
  job link resolve to the board). Big corporate tenants (banks, card networks) usually need
  `--search intern` — the connector refuses boards >2000 postings without it — plus the usual
  `--title-must-match` guard.
- a GitHub **raw** or **blob** file URL: `…/blob/<branch>/listings.json` → `github`,
  `…/blob/<branch>/README.md` → `markdown`

## Steps

1. **Classify the input.**
   - **Already a clean board URL** (one of the forms above) → skip to step 3.
   - **A company name** ("Stripe", "add Ramp") → research which ATS the company uses (step 2).
   - **A careers page** (`stripe.com/jobs`, `careers.x.com`) → it almost always embeds an ATS;
     find the real board host/token (step 2).
   - **A single job-posting link** (e.g. `jobs.lever.co/acme/<uuid>`) → the board token is the
     path segment right after the host; for a careers-page job link, find the underlying ATS.
   - **A bare GitHub repo** (`github.com/<u>/<r>`, no `/blob/…`) → resolve the default branch and
     the path to `listings.json` or `README.md`, then build the **raw** URL.

2. **Research (only when needed).** Use `WebSearch` / `WebFetch` to find the ATS and slug:
   - Search e.g. `"<company> greenhouse OR lever OR ashby OR workday careers"`, or fetch the
     careers page and look at the apply links — the host (`boards.greenhouse.io`,
     `jobs.lever.co`, `jobs.ashbyhq.com`, `*.myworkdayjobs.com`) and the slug/site after it
     give you the board.
   - For a GitHub list, use the repo's default branch + the file's path to construct
     `https://raw.githubusercontent.com/<u>/<r>/<branch>/<path>` (or the `…/blob/…` URL — the CLI
     normalizes blob→raw). **Confirm the resolved board URL with the user before writing.**

3. **Add via the CLI** (it detects the type, live fetch-tests, and writes — preserving comments):
   ```bash
   .venv/bin/python -m internshelper.sources add "<resolved-url>" --yes
   ```
   - Add `--label "<Name>"` for a nicer display name, `--title-must-match engineer,intern,...`
     for a mixed/retail-heavy board (flood guard).
   - Use `test` first if you just want to preview without writing:
     `.venv/bin/python -m internshelper.sources test "<resolved-url>"`.

4. **Report the result.** Show the CLI's fetch-test output (the posting count + sample titles). If
   it fetched **0**, the token is probably wrong — re-resolve rather than forcing it. If the CLI
   exits non-zero (bad URL / fetch error), explain what it said and try again; don't hand-edit
   `config/sources.yaml` to route around a failing fetch.

5. **Keep the selection private.** `config/sources.yaml` is ignored by Git. If the user wants the
   same sources on another machine, recommend a private backup or an explicit path via
   `INTERNSHELPER_SOURCES`. Never add the populated file to a public commit.

## Notes
- Prefer the CLI over editing YAML directly: it validates, fetch-tests, and keeps the file's
  comments intact. Hand-editing is the fallback only if the user explicitly wants an unusual entry.
- This complements `/review-internships`: this skill *adds* boards; that one *classifies* what
  they collect.
