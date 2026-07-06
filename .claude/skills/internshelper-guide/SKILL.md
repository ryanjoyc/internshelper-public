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
`config/sources.yaml`, keeps a de-duplicated archive (raw payloads saved to disk), and **emails a
nudge** when a batch piles up. Classification is **on-demand and free**: triage the pending queue
right in the dashboard (mark match/no-match, peek payloads), or open the repo in Claude Code and run
`/review-internships` for the agent-assisted pass — same verdicts either way. A Streamlit dashboard
shows confirmed matches, the (interactive) pending queue, source health, and source management —
in the browser, or as a Dock-launchable native macOS app (`InternsHELPer.app`). The
hourly cron is a dumb free collector; review is the classifier step — accurate, no API key, $0 ongoing.

## Architecture & data flow

```
collect (run.py) → store (store.py / db.py) → nudge (notify.py)
    → review on demand (in-app dashboard, or review.py + the review-internships skill) → dashboard (dashboard.py)
```

Each collect cycle: scrape every source → save each new posting's raw payload to
`data/payloads/{id}.json` → store it as `pending` → if `≥ notify_threshold` are pending (and you
haven't been nudged yet), email a one-line nudge. **No classification happens at collect time.**

Keyword flags (`is_internship` / `is_newgrad` / `is_cs_relevant`) are computed at collect time as
**priority hints only** — they order the review queue (candidates first). They never gate what gets
collected or shown.

## Module map (`internshelper/`)

**CLI entry points** (run as `.venv/bin/python -m internshelper.<name>`):

| Module | Responsibility |
|--------|----------------|
| `run` | Scheduled collector: fetch all sources, store new payloads as pending, compute priority hints, email the nudge. One invocation = one cycle. |
| `review` | On-demand review CLI: list pending, set verdicts, finish the queue, summarize confirmed matches. Driven by the `review-internships` skill. |
| `sources` | Add/list/remove/test job-board sources: detect URL type (with a `sniffer` fallback for boards embedded on careers pages), live fetch-test, append-only writes to `sources.yaml` (comments preserved). Driven by the `add-source` skill or the dashboard Sources tab. Exposes a reusable add core (`resolve_entry`, `is_duplicate`, `fetch_test`, `append_source`). |
| `setup` | Bootstrap: scaffold per-machine `.env` (secrets + feature toggles), realize the launchd plist and install the Dock app on macOS. Idempotent. |
| `app` | Dock-app runtime launcher: start the dashboard's Streamlit server headless on port 8510 (under a pipe-watchdog that reaps it if the launcher dies), show it in a native pywebview window, stop it on quit. Pidfile (`data/app.pid`) decides attach vs own for a pre-existing server. |
| `appbundle` | Build `InternsHELPer.app` into `build/` (Info.plist, launcher script execing `app`, `.icns` from `assets/icon-1024.png` via sips/iconutil); `--install` copies it to `~/Applications`. macOS-only. |

**Libraries:**

| Module | Responsibility |
|--------|----------------|
| `store` | Persistence: upsert postings, per-source close-detection, digest selection, run logging (UTC ISO-8601). |
| `db` | SQLite schema, connections, meta key/value store, runs-history pruning. |
| `config` | Load + validate `sources.yaml` and `settings.toml`; collects errors without halting on a single bad entry. |
| `models` | The normalized `Posting` dataclass shared across connectors / classify / store; carries scraped + classified fields + the raw payload. |
| `classify` | Keyword matcher (whole-word, case-insensitive) over title + HTML-stripped description → the three flag booleans. |
| `clock` | Canonical UTC ISO-8601 helpers: parse mixed date formats, convert to UTC, diff. |
| `notify` | Render + send the review-nudge email. |
| `display` | Pure display helpers: humanize mixed date formats with relative-age hints (no Streamlit dependency). |
| `dashboard` | Streamlit UI — Feed / Tracker / Health / Sources. Feed's "Pending review" is interactive (mark match/no_match, peek the raw payload, bulk-clear non-candidates); Sources adds/removes boards in-app. UI only; data logic lives in `store` / `review` / `sources`. |
| `dotenv` | Stdlib `.env` loader; shell env vars win; silent no-op if the file is missing. |
| `text` | HTML→plaintext stripper (stdlib `html.parser`) used before keyword matching. |
| `sourceurl` | Pure URL→`SourceEntry` detection (no network): map a pasted board URL to a source by host + path. |
| `sniffer` | Heuristic, no-AI detection of embedded Greenhouse/Lever/Ashby boards on an arbitrary careers page (regex over the fetched HTML). The add-source fallback when `sourceurl` can't resolve a clean board URL. |
| `filters` | Apply user filter booleans (`require_cs`, `require_intern_or_newgrad`) to classified postings. Pure function. |
| `digest` | Render + email a digest of new matching postings; watermark advances only after a confirmed send. |
| `__init__` | Package root (version). |

## Connectors (`internshelper/connectors/`)

Each parses one source type into `Posting`s. They self-register via the `base` registry, so adding
a type = adding a connector that registers itself + a `sourceurl` detection rule.

| Connector | Source |
|-----------|--------|
| `greenhouse` | Greenhouse public board API (`/v1/boards/{token}/jobs?content=true`). |
| `lever` | Lever public postings API (`/v0/postings/{token}`). |
| `ashby` | Ashby public board API (`/posting-api/job-board/{org}`); keeps `isListed=true` only. |
| `github_list` | Structured JSON internship lists (e.g. SimplifyJobs `listings.json`), active+visible rows. |
| `markdown_list` | Hand-maintained Markdown internship tables; auto-detects columns, handles `↳` continuation rows + `🔒` closed markers. |
| `base` | Base `Connector` class, shared HTTP helpers (httpx, timeouts, headers), and the type→connector registry. |

## The CLIs

Always run from the repo root with the project venv: `.venv/bin/python -m internshelper.<x>`.
Subcommands below; use `--help` (or read the module's argparse) for full flags.

- **`sources`** — `add <url>` · `list` · `remove <type:token>` · `test <url|source_key>`
- **`review`** — `list-pending` · `set-verdict <id> --verdict match|no_match [--reason ...]` · `finish` · `summary`
- **`run`** — no subcommands; one invocation runs one collection cycle (scheduled hourly by launchd).
- **`setup`** — no subcommands; interactive, or `--no-input` to read `INTERNSHELPER_*` env vars.
- **`app`** — no subcommands; what the Dock app runs. Needs the `app` extra (pywebview).
- **`appbundle`** — `[--install] [--repo-dir ...] [--target-dir ...]`; rebuild/reinstall the Dock app (e.g. after moving the repo).

## Config & data

- `config/sources.yaml` — the board list. **Git-committed and shared**; prefer the `sources` CLI
  over hand-editing it.
- `config/settings.toml` — neutral, committed settings (e.g. `[smtp] host`/`port`,
  `[review] notify_threshold`, classification keywords/filters).
- `.env` — **gitignored, per-machine**: SMTP secrets + `INTERNSHELPER_FEATURE_*` toggles
  (`COLLECT`/`EMAIL`/`SCHEDULE`/`DASHBOARD`/`APP`) and path overrides (`INTERNSHELPER_DB`/
  `_SOURCES`/`_SETTINGS`). A real exported env var always overrides the file.
- `data/` — **gitignored**: `data/internshelper.db` (SQLite) and `data/payloads/{id}.json` (raw
  payloads). Paths resolve off the repo root, so the scheduler's working directory doesn't matter.

## Run & test

```bash
# Setup (manual)
python3 -m venv .venv && .venv/bin/python -m pip install -e ".[dev,dashboard]"
# Or one-shot, per-machine config:
bash scripts/bootstrap.sh

.venv/bin/python -m pytest                              # tests (fixtures, no live calls)
.venv/bin/python -m internshelper.run                  # one collection cycle
.venv/bin/streamlit run internshelper/dashboard.py     # dashboard (browser)
.venv/bin/python -m internshelper.appbundle --install  # Dock app → ~/Applications (macOS)
```

## Conventions & gotchas

- **Always use `.venv/bin/python`** — not bare `python`.
- **Prefer the `sources` CLI or the dashboard Sources tab** over editing `config/sources.yaml` by
  hand; both validate + fetch-test + preserve comments.
- **All persisted timestamps are UTC ISO-8601 strings** (sort-safe), via `clock`.
- **Connectors self-register** through `base`'s registry — add the connector and a `sourceurl`
  rule rather than wiring it in elsewhere.
- **launchd doesn't fire while the Mac is asleep**; overnight postings land on the first cycle
  after wake (`RunAtLoad=true`).
- The classification *judgment* (made in-app or by the agent) is the deliberate human-in-the-loop
  step; the mechanics it drives are unit-tested — the `review` CLI, and the dashboard via `AppTest`.

## Related skills

- **`add-source`** — turns messy input (company name, careers page, job link, GitHub list) into a
  clean board URL and adds it via the `sources` CLI. Use it to *add* boards.
- **`review-internships`** — drives the `review` CLI to classify the pending queue. Use it to
  *classify* what the collector found.
- **`update-internshelper-guide`** — refresh THIS guide after a structural change.
