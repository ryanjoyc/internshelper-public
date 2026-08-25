---
name: internshelper-guide
description: Orientation map for the internsHELPer codebase. Use FIRST when starting work in this repo, or when the user says "how does internsHELPer work", "where is X", "navigate this codebase", "what does <module> do", or you need to find where to make a change. Gives the module map, data flow, the CLIs, config/data layout, and conventions — so you can locate the right file without scanning everything.
---

# internsHELPer: project guide

This is the map of the repo. Read it to orient, then go to the named source file for fine
detail. It is **mid-level on purpose** — it tells you *where* things live and *how the pieces
fit*, not every function signature. If you make a structural change, refresh this guide via the
**`update-internshelper-guide`** skill so it doesn't rot.

## What internsHELPer is

A local, $0 tool that **collects** internship / new-grad postings from boards listed in
`config/sources.yaml`, keeps a de-duplicated archive (raw payloads saved to disk), and **emails
a Top-target digest** when new postings arrive from explicitly prioritized companies. There is
**no approval gate**: every collected posting lands on the Board's Inbox, organized into Top
targets, Known companies, Worth discovering, and neutral Unclassified sections. Each company is
one expandable card containing all broadly collected roles; rank score cannot move or demote a
company or role. Curation is one-click: **dismiss** (hidden, undoable), **flag**
(⚑ — park suspect data like a dead link for investigation; hidden, undoable, NO training
signal), **drag to Applied** — and application/dismiss actions train the ranker.
`/review-internships` in Claude Code is the agent-assisted posting audit. The web UI
(`internshelper/web/` — FastAPI + Jinja + HTMX/Alpine, vendored, no build step) has five pages:
Board (company-grouped Inbox + the Applied → Interviewing → Offer → Rejected kanban — pipeline
lanes hide to thin strips — with table + dismissed + flagged + duplicate lenses), Postings
(searchable archive), Companies (browsing groups/discovery approvals plus board resolution),
Sources (add/remove wizard), Health — in the browser, or as a Dock-launchable native macOS app
(`InternsHELPer.app`). The hourly cron is a dumb free collector; company groups organize —
accurate, no API key, $0 ongoing.

## Architecture & data flow

```
collect (run.py) → store (store.py / db.py) → deduplicate (dedup.py)
    → rank hint (ranking.py) + company group (tiers.py / companygroups.py)
    → digest new Top-target arrivals (notify.py)
    → browse by company; dismiss / flag / drag-to-Applied (review.py + applications table)
```

Each collect cycle: scrape every source → save each new posting's raw payload to
`data/payloads/{id}.json` → store it straight into the Inbox → collapse strong cross-source URL
duplicates → retrain + rescore + apply the company-group map → email the Top-target digest for rows never digested before
(`notified_at` watermark, exactly-once per posting). **Nothing is gated.**

Keyword flags (`is_internship` / `is_newgrad` / `is_cs_relevant`) are computed at collect time
as **cold-start hints only**. The Board orders each company's roles newest-first. Learned
`rank_score` remains available as a hint but cannot choose a company group or hide a role.
Groups come only from `config/company-groups.yaml`; absent companies are Unclassified.
Discovery suggestions carry reason/evidence and require user approval.

## Module map (`internshelper/`)

**CLI entry points** (run as `.venv/bin/python -m internshelper.<name>`):

| Module | Responsibility |
|--------|----------------|
| `run` | Scheduled collector: fetch all sources, store new payloads into the Inbox, compute hint flags, retrain+rescore+regroup, email the Top-target digest (exactly-once per posting via `notified_at`). One invocation = one cycle. |
| `review` | Inbox-actions CLI + the Board's data layer: `list_inbox` (company-group filter), `dismiss`/`undo_dismiss` (the no_match plumbing, reused), legacy `pin_tier`/`unpin` compatibility helpers, `flag`/`unflag`/`list_flagged` (suspect-data parking, no training label), duplicate inspection/override helpers, guard-leak + closed-inbox hygiene (undoable batches), `payload_summary`, `refresh_ranking`. Driven by the `review-internships` + `deep-scan-source` + `investigate-flags` skills and the board routes. |
| `ranking` | Learned fit hint over title/JD tokens + source prior + recency. No company prior. Scores persist but do not determine Board groups or visibility. |
| `tiers` | Compatibility bridge that applies the authoritative company-group map to posting `tier` values. Rank and candidate flags are deliberately ignored. |
| `companygroups` | Independent Board browsing index (`config/company-groups.yaml`): Top target/Known assignments, neutral Unclassified default, aliases, and evidence-backed Discovery proposals with approve/reject. |
| `companies` | Separate board-resolution index (`config/companies.yaml`): add/list companies, record board proposals, approve (→ `sources.yaml`) / reject / link / mark no-board. It does not classify Board priority. |
| `dedup` | Cross-source duplicate handling: automatically collapse strong canonical-URL matches, surface same-company/title lookalikes for review, and preserve reversible keep/merge overrides. |
| `sources` | Add/list/remove/test job-board sources: detect URL type (with a `sniffer` fallback for boards embedded on careers pages), live fetch-test, append-only writes to `sources.yaml` (comments preserved). Driven by the `add-source` skill or the web UI's Sources page. Exposes a reusable add core (`resolve_entry`, `is_duplicate`, `fetch_test`, `append_source`, `parse_kv`). |
| `setup` | Bootstrap: scaffold per-machine `.env` (secrets + feature toggles), realize the launchd plist and install the Dock app on macOS. Idempotent. |
| `web` | The web UI server: `python -m internshelper.web [--port 8510]` (binds 127.0.0.1 only). A package, not a single file — `create_app()` factory in `__init__.py`, per-request DB connections in `deps.py`, routes/ (board — incl. the `/` and `/review` redirects, postings, health incl. `/healthz`, sources, companies), templates/ (Jinja + HTMX partials), static/ (app.css design tokens, app.js, vendored htmx/alpine/sortable, Geist fonts). UI only; data logic lives in `store` / `review` / `sources`. |
| `app` | Dock-app runtime launcher: start the web server headless on port 8510 (under a pipe-watchdog that reaps it if the launcher dies), probe `/healthz`, show it in a native pywebview window, stop it on quit. Pidfile (`data/app.pid`) decides attach vs own for a pre-existing server; caps `data/app.log` at launch. |
| `appbundle` | Build `InternsHELPer.app` into `build/` (Info.plist, launcher script execing `app`, `.icns` from `assets/icon-1024.png` via sips/iconutil); `--install` copies it to `~/Applications`. macOS-only. |

**Libraries:**

| Module | Responsibility |
|--------|----------------|
| `store` | Persistence: upsert postings, per-source close-detection, source-health/quiet detection, run logging (UTC ISO-8601). |
| `db` | SQLite schema, connections, meta key/value store, runs-history pruning. |
| `config` | Load + validate `sources.yaml` and `settings.toml`; collects errors without halting on a single bad entry. |
| `models` | The normalized `Posting` dataclass shared across connectors / classify / store; carries scraped + classified fields + the raw payload. |
| `classify` | Keyword matcher (whole-word, case-insensitive) over title + HTML-stripped description → the three flag booleans. |
| `clock` | Canonical UTC ISO-8601 helpers: parse mixed date formats, convert to UTC, diff. |
| `notify` | Render + send the Top-target digest email (HTML-escaped). |
| `display` | Pure display helpers: humanize mixed date formats with relative-age hints (framework-free). |
| `dotenv` | Stdlib `.env` loader; shell env vars win; silent no-op if the file is missing. |
| `text` | HTML→text helpers (stdlib `html.parser`, survives double-escaped HTML): `strip_html` one-liner for keyword/token matching, `html_to_text` paragraph/bullet-preserving for showing descriptions. |
| `sourceurl` | Pure URL→`SourceEntry` detection (no network): map a pasted board URL to a source by host + path. |
| `sniffer` | Heuristic, no-AI detection of embedded Greenhouse/Lever/Ashby/Workday boards on an arbitrary careers page (regex over the fetched HTML). The add-source fallback when `sourceurl` can't resolve a clean board URL. |
| `filters` | Apply user filter booleans (`require_cs`, `require_intern_or_newgrad`) to classified postings. Pure function. |
| `mailer` | Shared SMTP send helper used by `notify` (the v1 digest emailer is gone). |
| `__init__` | Package root (version). |

## Connectors (`internshelper/connectors/`)

Each parses one source type into `Posting`s. They self-register via the `base` registry, so adding
a type = adding a connector that registers itself + a `sourceurl` detection rule.

| Connector | Source |
|-----------|--------|
| `greenhouse` | Greenhouse public board API (`/v1/boards/{token}/jobs?content=true`). |
| `lever` | Lever public postings API (`/v0/postings/{token}`). |
| `ashby` | Ashby public board API (`/posting-api/job-board/{org}`); keeps `isListed=true` only. |
| `amazon` | Amazon Jobs search API (`/en/search.json`), paginated by result offset. |
| `github_list` | Structured JSON internship lists (e.g. SimplifyJobs `listings.json`), active+visible rows. |
| `markdown_list` | Hand-maintained Markdown internship tables; auto-detects columns, handles `↳` continuation rows + `🔒` closed markers. Per-source `columns:` override; the sentinel `columns: company=@heading` (opt-in) parses firm-per-section lists (`## Firm` heading + `\|Role\|Links\|` tables, e.g. the NUFT quant list). |
| `workday` | Workday CXS board API (`POST …/wday/cxs/{tenant}/{site}/jobs`, paginated by offset; banks/card networks). A partial fetch **raises** (never returns partial — close-detection would falsely close live postings); refuses boards >2000 postings unless the per-source `search:` key (server-side CXS searchText, warned loudly) narrows them. `posted_at=None` on purpose: `postedOn` is relative prose and `startDate` is the posting date. |
| `base` | Base `Connector` class, shared HTTP helpers (httpx `_get`/`_post_json`, timeouts, headers), and the type→connector registry. |

## The CLIs

Always run from the repo root with the project venv: `.venv/bin/python -m internshelper.<x>`.
Subcommands below; use `--help` (or read the module's argparse) for full flags.

- **`sources`** — `add <url> [--search …]` · `list` · `remove <type:token>` ·
  `test <url|source_key> [--json]` (`--json` dumps every parsed posting with its
  `posting_id`+`url` — used by `deep-scan-source`; `test` adopts the registered entry's extras
  (e.g. markdown `columns`) when the target is registered, so it parses exactly as collect does)
- **`review`** — `list-inbox [--tier T] [--limit N]` · `dismiss <id> [--reason ...]` · `undo-dismiss <id>` · `pin <id> --tier T` · `unpin <id>` · `flag <id> [--reason ...]` / `unflag <id>` / `list-flagged` (the flagged-for-review queue — `investigate-flags` drives these) · `applied` (applied posting_ids — the `deep-scan-source` guard) · `list-leaks` / `clear-leaks` (inbox rows failing their source's *current* title guard)
- **`ranking`** — `retrain` (rescore all inbox rows) · `show` (stored model summary) · `explain <posting_id>` (score + per-feature contributions) · `labels` (the unified training set, JSON) · `eval [--holdout 0.25] [--k 10,25,50]` (time-ordered backtest over all label signals)
- **`companies`** — `add <name>` · `list [--json]` · `propose <name> --url ... [--count N] [--evidence ...]` · `approve <name>` · `reject <name>` · `resolve-write <name> <source_key>` · `mark-no-board <name>` (legacy `set-tier` is retained but ignored by Board grouping)
- **`companygroups`** — `list` · `set <name> top_target|known` · `clear <name>` · `propose <name> discovery --reason ... --evidence ...` · `approve <name>` · `reject <name>` (Discovery cannot bypass proposal approval)
- **`run`** — no subcommands; one invocation runs one collection cycle (scheduled hourly by launchd).
- **`setup`** — no subcommands; interactive, or `--no-input` to read `INTERNSHELPER_*` env vars.
- **`web`** — `[--port 8510]`; serves the web UI on 127.0.0.1. Needs the `web` extra.
- **`app`** — no subcommands; what the Dock app runs (spawns `web` + native window). Needs the `app` extra (pywebview).
- **`appbundle`** — `[--install] [--repo-dir ...] [--target-dir ...]`; rebuild/reinstall the Dock app (e.g. after moving the repo).

## Config & data

- `config/sources.yaml` — the board list. **Git-committed and shared**; prefer the `sources` CLI
  over hand-editing it.
- `config/companies.yaml` — the approved-companies index (**machine-managed** — regenerated on
  every write; edit via the `companies` CLI or the web Companies page, never by hand).
- `config/company-groups.yaml` — the independent Board browsing groups (**machine-managed**);
  use the `companygroups` CLI or Companies/Board controls. Missing companies are unclassified.
- `config/profile.md` — the user's role-fit profile. The `deep-scan-source` / `review-internships`
  agents judge every posting's JD against it (generous, JD-over-title).
- `config/settings.toml` — neutral, committed settings (e.g. `[smtp] host`/`port`,
  classification keywords/filters).
- `.env` — **gitignored, per-machine**: SMTP secrets + `INTERNSHELPER_FEATURE_*` toggles
  (`COLLECT`/`EMAIL`/`SCHEDULE`/`DASHBOARD`/`APP`) and path overrides (`INTERNSHELPER_DB`/
  `_SOURCES`/`_SETTINGS`). A real exported env var always overrides the file.
- `data/` — **gitignored**: `data/internshelper.db` (SQLite) and `data/payloads/{id}.json` (raw
  payloads). Paths resolve off the repo root, so the scheduler's working directory doesn't matter.

## Documentation entrypoints

- `STATE.md` — living current-focus, runtime, Git, and active-doc dashboard. Read first; verify its
  time-sensitive claims live when they matter.
- `docs/README.md` — documentation catalog and lifecycle rules.
- `README.md` — setup and operator workflows.
- `ROADMAP.md` — forward-looking product direction only.
- `docs/source-coverage.md` — connector support, boundaries, and acceptance criteria.
- Completed or superseded plans are intentionally absent from the active tree; use Git history.

## Run & test

```bash
# Setup (manual)
python3 -m venv .venv && .venv/bin/python -m pip install -e ".[dev,web]"
# Or one-shot, per-machine config:
bash scripts/bootstrap.sh

.venv/bin/python -m pytest                              # tests (fixtures, no live calls)
.venv/bin/python -m internshelper.run                  # one collection cycle
.venv/bin/python -m internshelper.web                  # web UI → http://127.0.0.1:8510
.venv/bin/python -m internshelper.appbundle --install  # Dock app → ~/Applications (macOS)
```

## Conventions & gotchas

- **Always use `.venv/bin/python`** — not bare `python`.
- **Prefer the `sources` CLI or the web UI's Sources page** over editing `config/sources.yaml` by
  hand; both validate + fetch-test + preserve comments.
- **All persisted timestamps are UTC ISO-8601 strings** (sort-safe), via `clock`.
- **Connectors self-register** through `base`'s registry — add the connector and a `sourceurl`
  rule rather than wiring it in elsewhere.
- **launchd doesn't fire while the Mac is asleep**; overnight postings land on the first cycle
  after wake (`RunAtLoad=true`).
- The curation *judgment* (dismiss/flag/apply, made in-app or by the agent) is the deliberate
  human-in-the-loop step; the mechanics it drives are unit-tested — the `review` CLI, and the
  web UI via FastAPI `TestClient` (`tests/test_web*.py`; fixtures in `tests/conftest.py`).
- Application/dismiss mutations refresh ranking best-effort; company-group changes rewrite all
  matching posting group values directly. Dismissed = `verdict='no_match'` (old plumbing).

## Related skills

- **`add-source`** — turns messy input (company name, careers page, job link, GitHub list) into a
  clean board URL and adds it via the `sources` CLI. Use it to *add* boards.
- **`resolve-companies`** — researches each *pending* company in the approved-companies index
  into a board **proposal** (any supported ATS), fetch-tested, for the user's Approval A on the web
  Companies page. Never approves on its own.
- **`review-internships`** — drives the `review` CLI to audit postings generously from saved
  payloads; it dismisses confirmed junk but does not choose company groups.
- **`deep-scan-source`** — exhaustively *verify* one source against a specific term (e.g. "Summer
  2027") by opening **every** posting's live link with a subagent, sorting each into
  match/uncertain/no_match, and writing dismiss/restore actions for a registered source. The strict, per-link
  counterpart to `review-internships`. Enumerates via `sources test --json`. Token-heavy by design.
- **`investigate-flags`** — clears the Board's ⚑ flagged-for-review queue: opens each flagged
  posting live (ATS JSON-API fallbacks), re-enumerates its source to compare URLs, then unflags
  false alarms, dismisses confirmed-gone postings, and diagnoses connector/URL bugs. The deepest
  per-posting pass of the three.
- **`update-internshelper-guide`** — refresh THIS guide after a structural change.
