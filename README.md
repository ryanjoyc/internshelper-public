# internsHELPer

A local-first internship collection and review app. It **collects** internship and new-grad
postings from the boards you choose, keeps a de-duplicated archive on your machine, and groups the
live Board by company priority: Top targets, Known companies, Worth discovering, and neutral
Unclassified. On macOS, the primary experience is a locally built Dock app; the same dashboard can
also run in a browser. It tracks applications, the full archive, source health, and evidence-backed
discovery proposals without a hosted service or frontend build step.

New postings also carry a separate availability state. The collector validates destinations
before first display, keeps ambiguous failures visible with safer actions, archives only
confirmed-closed ordinary roles, and preserves saved or applied roles as visible closed history.
Availability never trains or changes the ranker.

**Why this shape:** keyword matching alone is too noisy to decide what a person should see. The
collector therefore stays broad and local; company groups are explicit user choices, rank score is
only a hint, and an agent can audit saved payloads or research lesser-known companies on demand.

- **Sources:** Greenhouse, Lever, Ashby, Workday, Amazon Jobs, and community GitHub listings.
  You maintain the list in `config/sources.yaml`.
- **Keyword flags** (`is_internship`/`is_newgrad`/`is_cs_relevant`) are computed at collect time as
  **hints only**; they never choose a company group or hide a posting.
- **Runs locally** in a browser or a native macOS window installed in the Dock.
- **Automation is opt-in:** manual collection works without email or a background schedule.

## Documentation

- [`STATE.md`](STATE.md) — current focus, runtime caveats, Git snapshot, and active-doc index.
- [`docs/README.md`](docs/README.md) — reading order and documentation lifecycle rules.
- [`ROADMAP.md`](ROADMAP.md) — forward-looking product direction.
- [`docs/source-coverage.md`](docs/source-coverage.md) — connector support and known boundaries.

Agents should start with `AGENTS.md` (or `CLAUDE.md`) and the repository-local
`internshelper-guide` skill rather than scanning the tree from scratch.

## Quick start (clone-and-go)

On a fresh clone, one command creates the virtual environment, installs the dashboard dependencies
and, on macOS, the Dock-app dependencies, then scaffolds this machine's configuration:

```bash
git clone https://github.com/ryanjoyc/internshelper-public.git internshelper && cd internshelper
bash scripts/bootstrap.sh
```

It creates private working copies of the example configuration, then asks about availability
checks, the dashboard, and installation of `InternsHELPer.app` in `~/Applications`. Optional email
digests and hourly launchd scheduling are offered but default to off. The generated `.env` and
mutable files under `config/` are ignored by Git. Bootstrap is idempotent: it reuses the virtual
environment and never overwrites existing configuration.

`bash scripts/bootstrap.sh --no-input` uses safe defaults and exported `INTERNSHELPER_*` values.
It leaves email, scheduling, and Dock installation off unless you explicitly enable them; on macOS,
set `INTERNSHELPER_FEATURE_APP=1` when you want an unattended bootstrap to install the Dock app.

The ignore rules reduce accidental commits; they do not replace checking `git status` before each
commit. Never commit a populated `.env`, application database, raw payload, or personal profile.

Add a board, run the first collection, and open the app:

```bash
.venv/bin/python -m internshelper.sources add https://boards.greenhouse.io/stripe
.venv/bin/python -m internshelper.run
open "$HOME/Applications/InternsHELPer.app"   # macOS, if installed during setup
```

For the browser interface, run `.venv/bin/python -m internshelper.web` and open
<http://127.0.0.1:8510>. Without optional scheduling, rerun `internshelper.run` whenever you want to
refresh the local archive.

The rest of this README is the manual/advanced reference behind that one command.

## 1. Install (manual)

Requires Python ≥ 3.11.

```bash
cd internshelper
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev,app]"  # macOS: dashboard + Dock app
.venv/bin/python -m internshelper.setup
```

On Linux or for a browser-only installation, use `.[dev,web]` instead of `.[dev,app]`.

The setup helper creates `.env` with owner-only permissions and seeds missing private config files
from their tracked examples. Edit `config/profile.md` before using the agent-assisted review
skills. The other generated config files start empty and are populated through the CLIs or web
dashboard.

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
job link** instead of a clean board URL, run **`/add-source`** in a supported coding agent
workspace; the agent researches it, resolves it to a board, and calls the CLI for you.

`config/sources.yaml` is per-user and ignored by Git. Back it up privately if you want to share the
same source list across your own machines. Keep reusable examples in
`config/sources.example.yaml` free of personal selections.

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

## 3. Run the collector

The collector loads local configuration itself, so no inline environment variables are needed:

```bash
.venv/bin/python -m internshelper.run
```

Each run: scrape → apply each source's coarse title guard → save raw payloads under
`data/payloads/` → upsert the local archive → validate new destinations → collapse strong URL
duplicates and reconcile their availability evidence → refresh ranking hints and company groups.
When optional email is enabled, it then sends an exactly-once Top-target digest. Community-list
rows remain hidden until their destination check finishes. Data lives in
`data/internshelper.db` (gitignored); on macOS and Linux, new database, payload, and app-log
files use owner-only permissions.

Set `INTERNSHELPER_FEATURE_COLLECT=0` to disable a machine's collector,
`INTERNSHELPER_FEATURE_AVAILABILITY=0` to retain legacy collection without destination checks, or
`INTERNSHELPER_FEATURE_EMAIL=1` to add email digests.

## 4. Audit postings with the agent (optional)

First customize `config/profile.md`. To inspect saved payloads for obvious junk without narrowing
the Board prematurely, run:

```
/review-internships
```

The agent reads the current Inbox payloads and dismisses only confirmed junk. It does not choose
company groups or filter out uncertain roles. It runs in the current coding-agent session, so the
app needs no separate AI API key or per-posting service.

Manual CLI (what the skill drives) if you want it without the skill:

```bash
.venv/bin/python -m internshelper.review list-inbox
.venv/bin/python -m internshelper.review dismiss <posting_id> --reason "..."
.venv/bin/python -m internshelper.review undo-dismiss <posting_id>
.venv/bin/python -m internshelper.review list-flagged
```

## 5. Dock app and dashboard

### Dock app (macOS)

The macOS setup flow offers to install the dashboard as `~/Applications/InternsHELPer.app`. To
build or reinstall it directly:

```bash
.venv/bin/python -m internshelper.appbundle --install
open "$HOME/Applications/InternsHELPer.app"
```

The app has its own Dock icon and native window. Opening it starts the local web server headlessly
on port 8510; quitting the window stops the server. The bundle is a small launcher over the current
checkout and needs the `app` extra (`pip install -e ".[app]"`, macOS only).

Notes:

- If a healthy server is already running on port 8510, the app attaches to it and leaves it running
  on quit. If another process occupies that port without passing the health check, the app asks you
  to quit that process or reboot instead of trying to kill an unverified process.
- The repo path is baked into the bundle at build time. If you move the repo, run `--install` again.
- The bundle is unsigned. A local build runs normally; a copy received through AirDrop or a zip may
  need `xattr -dr com.apple.quarantine InternsHELPer.app`.
- Server logs land in the gitignored `data/app.log` if the window comes up blank.

### Browser interface

```bash
.venv/bin/python -m internshelper.web        # serves http://127.0.0.1:8510
```

The dashboard uses FastAPI, Jinja, and vendored HTMX/Alpine with no Node build step. It binds only
to `127.0.0.1` and includes:

- **Board** — every broadly collected posting, organized into Top targets, Known companies,
  Worth discovering, and neutral Unclassified. Each company is one expandable card containing
  all of its roles; rank score never hides or demotes them. Drag roles into Applied →
  Interviewing → Offer / Rejected, or use the table/dismissed/flagged/duplicates lenses.
  Availability notices guard broken links, keep browser-only barriers actionable, and offer
  replacement links for explicit confirmation. **Verify and find application** reports semantic
  progress without changing the stored URL until you confirm a replacement.
- **Companies** — manage the separate browsing-group index and approve evidence-backed
  Worth discovering proposals; career-board resolution remains an independent workflow.
- **Postings** — search the full archive and filter by state. No-match verdicts are archived and
  remain reachable.
- **Sources** — add a board through a live fetch-test wizard or remove one with confirmation.
  Source-action HTTP requests must come from the same loopback browser origin; use the `sources`
  CLI for automation.
- **Health** — inspect each source's latest run, count against baseline, and quiet/error signals.

## 6. Optional automation

Email digests and background scheduling are disabled by default and are not required to collect or
browse postings. Their configuration and failure behavior have automated coverage, but SMTP and
launchd depend on the machine where they run. Test each one locally before relying on unattended
delivery.

### Email digests (SMTP)

When email is enabled, each newly collected Top-target posting is included once in the digest. The
sender, recipient, and password are per-machine secrets stored only in the gitignored `.env`.
`config/settings.toml` contains the neutral SMTP host and port.

For Gmail, enable 2-Step Verification, create an
[App Password](https://myaccount.google.com/apppasswords), then set:

```dotenv
INTERNSHELPER_FEATURE_EMAIL=1
INTERNSHELPER_SMTP_SENDER=you@gmail.com
INTERNSHELPER_SMTP_RECIPIENT=you@gmail.com
INTERNSHELPER_SMTP_PASSWORD=your-16-char-app-password
```

The collector uses STARTTLS with certificate verification before sending credentials. Other SMTP
providers may require different host and port values in `config/settings.toml`.

### Hourly collection (launchd)

On macOS, explicitly opt in to realize and load the per-machine launchd job:

```bash
INTERNSHELPER_FEATURE_SCHEDULE=1 .venv/bin/python -m internshelper.setup --no-input
```

The generated plist contains the checkout and log paths but no SMTP credentials. Logs go to
`~/Library/Logs/internshelper`. The job runs hourly and once when loaded; because launchd does not
run while the Mac sleeps, missed work starts after wake. To stop it:

```bash
launchctl bootout "gui/$(id -u)/com.internshelper.run"
```

On Linux, use cron or systemd to invoke the project interpreter. The collector resolves config and
data paths from the repo, so the scheduler does not need a working directory:

```cron
0 * * * * /path/to/internshelper/.venv/bin/python -m internshelper.run
```

## 7. `.env` and feature toggles

The gitignored, per-machine `.env` holds secrets and feature choices. Exported environment
variables override the file. These are the safe unattended defaults:

```dotenv
INTERNSHELPER_SMTP_SENDER=
INTERNSHELPER_SMTP_RECIPIENT=
INTERNSHELPER_SMTP_PASSWORD=
INTERNSHELPER_FEATURE_COLLECT=1
INTERNSHELPER_FEATURE_EMAIL=0
INTERNSHELPER_FEATURE_AVAILABILITY=1
INTERNSHELPER_FEATURE_SCHEDULE=0
INTERNSHELPER_FEATURE_DASHBOARD=1
INTERNSHELPER_FEATURE_APP=0
```

Interactive setup on macOS offers the Dock app by default while leaving email and scheduling off.
Unattended setup also leaves app installation off unless `INTERNSHELPER_FEATURE_APP=1` is exported.
You can move mutable files outside the checkout with `INTERNSHELPER_DB`,
`INTERNSHELPER_SOURCES`, `INTERNSHELPER_SETTINGS`, `INTERNSHELPER_COMPANIES`, and
`INTERNSHELPER_COMPANY_GROUPS`. See `.env.example` for the full template.

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

Connector tests run against frozen, sanitized public-response fixtures (no live calls);
store/run/notify/review logic
(close-detection, payload capture, Top-target digest, company grouping, reversible deduplication,
and schema migrations) is unit-tested. The portability layer is covered too: URL→source detection
(`test_sourceurl`), the sources CLI incl. comment-preserving writes (`test_sources`), the `.env`
loader + feature toggles (`test_dotenv`), and the bootstrap/`.env`-scaffold/plist guard
(`test_setup`). Agent research and fit judgment remain deliberate human-in-the-loop steps; the
deterministic CLIs and web mutations they drive are tested.

## License and third-party software

No license has been selected for the original internsHELPer source code. Making this repository
public does not grant permission to copy, modify, or redistribute that project-authored code. Add a
project license before inviting reuse or contributions.

The vendored browser libraries, fonts, and repository-local pstack adaptation remain under their
own licenses. See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and the license files it links.
