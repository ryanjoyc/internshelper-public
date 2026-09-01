# internsHELPer

A local, $0 tool that **collects** internship / new-grad postings from the boards you list,
keeps a de-duplicated archive (full raw payloads saved to disk), and groups the live Board by
company priority: Top targets, Known companies, Worth discovering, and neutral Unclassified.
Each company is one expandable card containing all broadly collected roles. A local web dashboard
(FastAPI + HTMX, no build step) also tracks applications, the full archive, source health, and
evidence-backed discovery proposals.

New postings also carry a separate availability state. The collector validates destinations
before first display, keeps ambiguous failures visible with safer actions, archives only
confirmed-closed ordinary roles, and preserves saved or applied roles as visible closed history.
Availability never trains or changes the ranker.

**Why this shape:** keyword matching alone is too noisy to decide what the user should see. The hourly
collector therefore stays broad and free; company groups are explicit user choices, rank score is
only a hint, and an agent can audit saved payloads or research lesser-known companies on demand.

- **Sources:** Greenhouse, Lever, Ashby, Workday, Amazon Jobs, and community GitHub listings.
  You maintain the list in `config/sources.yaml`.
- **Keyword flags** (`is_internship`/`is_newgrad`/`is_cs_relevant`) are computed at collect time as
  **hints only**; they never choose a company group or hide a posting.
- **Runs locally**, scheduled hourly by macOS `launchd`.

## Documentation

- [`STATE.md`](STATE.md) — current focus, runtime caveats, Git snapshot, and active-doc index.
- [`docs/README.md`](docs/README.md) — reading order and documentation lifecycle rules.
- [`ROADMAP.md`](ROADMAP.md) — forward-looking product direction.
- [`docs/source-coverage.md`](docs/source-coverage.md) — connector support and known boundaries.

Agents should start with `AGENTS.md` (or `CLAUDE.md`) and the repository-local
`internshelper-guide` skill rather than scanning the tree from scratch.

## Quick start (clone-and-go)

On a fresh clone, one command sets everything up — creates the venv, installs deps, and
scaffolds this machine's config:

```bash
git clone <repo-url> && cd internsHELPer
bash scripts/bootstrap.sh
```

It prompts for what *this* machine should do — email nudges (and your SMTP creds), availability
checks, the hourly schedule (macOS launchd), and the dashboard — then writes a **gitignored `.env`**
with your answers.
Secrets never leave the machine via git. It's **idempotent**: re-running reuses the venv and
never overwrites an existing `.env`. Use `bash scripts/bootstrap.sh --no-input` to accept
defaults (reads any `INTERNSHELPER_*` you've already exported).

Then add a board and you're live:

```bash
.venv/bin/python -m internshelper.sources add https://boards.greenhouse.io/stripe
```

The rest of this README is the manual/advanced reference behind that one command.

## 1. Install (manual)

Requires Python ≥ 3.11.

```bash
cd internsHELPer
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev,web]"
```

## 2. Configure sources

**The easy way — the CLI.** Paste a job-board URL; it auto-detects the type + slug, live
fetch-tests it (so a typo'd token is caught *now*, not silently next cycle), and only then
appends it to `config/sources.yaml` (your comments are preserved):

```bash
.venv/bin/python -m internshelper.sources add https://boards.greenhouse.io/stripe
.venv/bin/python -m internshelper.sources add https://jobs.lever.co/leverdemo --yes
.venv/bin/python -m internshelper.sources list
.venv/bin/python -m internshelper.sources test https://jobs.ashbyhq.com/ramp   # fetch, no write
.venv/bin/python -m internshelper.sources remove greenhouse:stripe
```

Accepted URLs: `boards.greenhouse.io/<token>`, `jobs.lever.co/<token>`,
`jobs.ashbyhq.com/<org>`, Workday careers sites, and GitHub lists (a `…/blob/…/listings.json` → `github`, a
`…/blob/…/README.md` → `markdown`). For a **company name**, a **careers page**, or a **single
job link** instead of a clean board URL, open the repo in Claude Code and run **`/add-source`**
— the agent researches it, resolves it to a board, and calls the CLI for you.

`config/sources.yaml` is **git-committed and shared** — `git commit && git push` after adding,
and your other machines (or other users) `git pull` to get the same list.

**The manual way.** Edit `config/sources.yaml` directly — one entry per source (many may share
a type):

```yaml
sources:
  - type: greenhouse
    token: stripe          # the slug in boards.greenhouse.io/<token>
    label: Stripe
  - type: lever
    token: leverdemo       # the slug in jobs.lever.co/<token>
  - type: ashby
    org: ramp              # the slug in jobs.ashbyhq.com/<org>
  # A GitHub Markdown-table list (README table, the common kind) — url is the RAW README:
  - type: markdown
    url: https://raw.githubusercontent.com/sndsh404/summer-2027-internships/main/README.md
    label: sndsh404 Summer 2027
  # Flood guard for a mixed/retail-heavy board — only ingest matching titles:
  # - type: greenhouse
  #   token: somebigco
  #   title_must_match: [engineer, intern, developer, data, software]
```

**Source types:** `greenhouse`/`lever` (`token`), `ashby` (`org`), `workday` (`url` plus optional
server-side `search`), `amazon` (`query`), `github` (structured `listings.json` `url`), and
`markdown` (a curated README-table list — `url` is the RAW README; columns auto-detect, `↳`
continuation rows + `🔒` closed markers handled). Prefer focused ATS
boards / curated Markdown lists over the 16k-entry Simplify JSON list (which would flood the queue).

## 3. Configure email (Gmail)

Your email + password are **per-machine secrets**, so they live in the gitignored `.env`, not
in a committed file (`bash scripts/bootstrap.sh` writes them for you). `config/settings.toml`
keeps only the neutral `[smtp] host`/`port`; `INTERNSHELPER_SMTP_SENDER` / `…_RECIPIENT` in
`.env` override the placeholder sender/recipient there. For Gmail:
1. Enable **2-Step Verification**.
2. Create an **App Password**: <https://myaccount.google.com/apppasswords>.
3. Put that 16-char value in `.env` as `INTERNSHELPER_SMTP_PASSWORD`.

```dotenv
# .env (gitignored, per-machine)
INTERNSHELPER_SMTP_SENDER=you@gmail.com
INTERNSHELPER_SMTP_RECIPIENT=you@gmail.com
INTERNSHELPER_SMTP_PASSWORD=your-16-char-app-password
```

Don't want email at all? Set `INTERNSHELPER_FEATURE_EMAIL=0` (see [§8](#8-env--feature-toggles))
and skip the creds — the Board remains fully usable.

When email is enabled, each newly collected Top-target posting is included once in the digest.

## 4. Run the collector

With creds in `.env`, the collector loads them itself — no inline env var needed:

```bash
.venv/bin/python -m internshelper.run
```

Each run: scrape → apply each source's coarse title guard → save raw payloads under
`data/payloads/` → upsert the local archive → validate new destinations → collapse strong URL
duplicates and reconcile their availability evidence → refresh ranking hints and company groups
→ send the exactly-once Top-target digest. Community-list rows remain hidden until their
destination check finishes. Data lives in `data/internshelper.db` (gitignored).

Set `INTERNSHELPER_FEATURE_COLLECT=0` to disable a machine's collector,
`INTERNSHELPER_FEATURE_AVAILABILITY=0` to retain legacy collection without destination checks, or
`INTERNSHELPER_FEATURE_EMAIL=0` to collect without emailing.

## 5. Audit postings with the agent (optional)

To inspect saved payloads for obvious junk without narrowing the Board prematurely, run:

```
/review-internships
```

The agent reads the current Inbox payloads and dismisses only confirmed junk. It does not choose
company groups or filter out uncertain roles. It's free — it runs in the current coding-agent
session, with no API key or per-posting charge.

Manual CLI (what the skill drives) if you want it without the skill:

```bash
.venv/bin/python -m internshelper.review list-inbox
.venv/bin/python -m internshelper.review dismiss <posting_id> --reason "..."
.venv/bin/python -m internshelper.review undo-dismiss <posting_id>
.venv/bin/python -m internshelper.review list-flagged
```

## 6. Dashboard

```bash
.venv/bin/python -m internshelper.web        # serves http://127.0.0.1:8510
```

A local web app (FastAPI + Jinja + HTMX/Alpine, all vendored — no Node, no build step),
system-aware dark/light theme, served on 127.0.0.1 only:

- **Board** — every broadly collected posting, organized into Top targets, Known companies,
  Worth discovering, and neutral Unclassified. Each company is one expandable card containing
  all of its roles; rank score never hides or demotes them. Drag roles into Applied →
  Interviewing → Offer / Rejected, or use the table/dismissed/flagged/duplicates lenses.
  Availability notices guard broken links, keep browser-only barriers actionable, and offer
  replacement links for explicit confirmation. **Verify and find application** reports semantic
  progress without changing the stored URL until you confirm a replacement.
- **Companies** — manage the separate browsing-group index and approve evidence-backed
  "Worth discovering" proposals; career-board resolution remains an independent workflow.
- **Postings** — the full archive with search and state filters; no-match verdicts are
  archived (dimmed, reachable), never deleted.
- **Sources** — add a board with a live fetch-test wizard (sniffer fallback for careers
  pages, "Add anyway" gate on empty fetches), remove with confirm. Source-action HTTP requests
  must come from the same loopback browser origin; use the `sources` CLI for automation.
- **Health** — per-source status cards: last run, count vs baseline, quiet/error flags.

### Dock app (macOS)

Run the dashboard like a real app — its own Dock icon, a native window, no browser:

```bash
.venv/bin/python -m internshelper.appbundle --install
```

That builds `InternsHELPer.app` (into the gitignored `build/`) and copies it to
`~/Applications`; drag it onto the Dock from there. Clicking it starts the web server
headless on port 8510 and opens a native window; quitting the window stops the server.
`bash scripts/bootstrap.sh` offers to do all of this (toggle: `INTERNSHELPER_FEATURE_APP`).
Needs the `app` extra (`pip install -e ".[app]"` — pywebview + the pyobjc frameworks, macOS only).

Notes:

- If a previous window crashed and left its server running, the next launch re-attaches to it
  (and still cleans it up on quit). A server *you* started by hand on 8510 is attached to but
  never killed.
- The repo path is baked into the bundle at build time — if you move the repo, re-run
  `--install` (same as the launchd plist).
- The bundle is unsigned. Built locally it runs without prompts; if you ever copy it to another
  Mac via AirDrop/zip, clear quarantine first: `xattr -dr com.apple.quarantine InternsHELPer.app`.
- Server logs land in `data/app.log` if the window comes up blank.

## 7. Schedule the collector (launchd)

`bash scripts/bootstrap.sh` already offers to do this. To (re)install it yourself, let the setup
helper realize + load the plist (it only templates per-machine paths — the password lives in
`.env`, not the plist). The realized job starts through `/usr/bin/env` so launchd can use a venv
inside a macOS-protected Documents checkout:

```bash
.venv/bin/python -m internshelper.setup --no-input   # honors INTERNSHELPER_FEATURE_SCHEDULE
```

Or do it by hand (no secret placeholder anymore):

```bash
REPO_DIR="$(pwd)"
LOG_DIR="$HOME/Library/Logs/internshelper"
mkdir -p "$LOG_DIR"
sed -e "s#__REPO_DIR__#${REPO_DIR}#g" -e "s#__LOG_DIR__#${LOG_DIR}#g" \
    scripts/com.internshelper.run.plist.template \
    > ~/Library/LaunchAgents/com.internshelper.run.plist

launchctl load ~/Library/LaunchAgents/com.internshelper.run.plist
launchctl kickstart -k gui/$(id -u)/com.internshelper.run   # fire one cycle now
```

> `launchd` does not fire while the Mac is asleep; overnight postings are collected on the first
> cycle after wake (`RunAtLoad=true`). The collector is unattended; refresh the Board when convenient.

To stop: `launchctl unload ~/Library/LaunchAgents/com.internshelper.run.plist`.

**Linux (cron/systemd).** No launchd, but the collector resolves its config + data paths off the
repo root, so it doesn't matter what working directory the scheduler uses — just call the venv
Python with an absolute path:

```cron
0 * * * * /path/to/internsHELPer/.venv/bin/python -m internshelper.run
```

(For systemd, a `*.service` running that command on an hourly `*.timer` works the same way.)

## 8. .env & feature toggles

The gitignored, per-machine `.env` (written by bootstrap) holds your secrets and which parts run.
A real exported environment variable always overrides the file.

```dotenv
INTERNSHELPER_SMTP_SENDER=you@gmail.com
INTERNSHELPER_SMTP_RECIPIENT=you@gmail.com
INTERNSHELPER_SMTP_PASSWORD=your-16-char-app-password
INTERNSHELPER_FEATURE_COLLECT=1     # run the hourly collector on this machine
INTERNSHELPER_FEATURE_EMAIL=1       # send Top-target digests (needs the SMTP creds above)
INTERNSHELPER_FEATURE_AVAILABILITY=1 # validate new links and enable availability actions
INTERNSHELPER_FEATURE_SCHEDULE=1    # install the launchd schedule (macOS)
INTERNSHELPER_FEATURE_DASHBOARD=1   # use the web dashboard
INTERNSHELPER_FEATURE_APP=1         # install the Dock app (macOS; needs DASHBOARD)
```

Set any `INTERNSHELPER_FEATURE_*` to `0` to turn that part off — e.g. a second machine that only
curates sources + views the dashboard can set `COLLECT=0` and `EMAIL=0`. Other paths
(`INTERNSHELPER_DB`, `INTERNSHELPER_SOURCES`, `INTERNSHELPER_SETTINGS`) are read here too.

## Tests

```bash
.venv/bin/python -m pytest
```

The default offline suite also validates the approved availability corpus, its frozen fixtures,
generated [case catalog](docs/availability-verification-catalog.md), coverage matrix, and mutant
scorecard. The separate acceptance contract exercises all 109 approved layer expectations; its
Board layer drives deterministic complete, partial, and absence snapshots through the production
collection orchestration and temporary SQLite databases:

```bash
.venv/bin/python -m pytest -q -m availability_contract  # 109 production-contract cases
.venv/bin/python -m pytest -q -m live_canary -s          # optional read-only observations; not a gate
```

Optional browser-level UI checks cover responsive breakpoints and overflow, disclosure/drawer
keyboard behavior, pipeline status persistence and errors, HTMX replacement focus, and source
removal confirmation:

```bash
.venv/bin/python -m pip install -e ".[ui]"
.venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest -m browser
```

Connector tests run against frozen real fixtures (no live calls); store/run/notify/review logic
(close-detection, payload capture, Top-target digest, company grouping, reversible deduplication,
and schema migrations) is unit-tested. The portability layer is covered too: URL→source detection
(`test_sourceurl`), the sources CLI incl. comment-preserving writes (`test_sources`), the `.env`
loader + feature toggles (`test_dotenv`), and the bootstrap/`.env`-scaffold/plist guard
(`test_setup`). Agent research and fit judgment remain deliberate human-in-the-loop steps; the
deterministic CLIs and web mutations they drive are tested.
