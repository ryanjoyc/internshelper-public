# internsHELPer

internsHELPer is a local-first app for collecting, reviewing, and tracking internships and new-grad roles. It pulls from job boards you choose, deduplicates postings, checks whether links are still live, and keeps your archive and application pipeline on your Mac. You can use it through a browser dashboard or a locally installed macOS Dock app.

## Built for agent-assisted work

internsHELPer separates repeatable work from open-ended research.

The application handles collection, storage, availability checks, and application tracking through tested Python commands and a local SQLite database. Codex handles the messier work: resolving careers pages into supported job boards, reviewing saved descriptions against your preferences, researching unfamiliar companies, and investigating questionable links.

This gives the agent a durable workspace with your chosen sources, saved evidence, and previous decisions. Each session can continue from that state instead of beginning another one-off web search. The agent runs through your existing Codex session; internsHELPer does not require a separate AI API key.

## Start with Codex

Open a local Codex task on your Mac and paste:

```text
Work locally on my Mac. Clone
https://github.com/ryanjoyc/internshelper-public.git and open that checkout.

Read AGENTS.md and the repository-local internshelper-guide before acting.
Run bash scripts/bootstrap.sh, install the macOS Dock app, and leave email
digests and background scheduling disabled. Help me customize my private role
profile without inventing preferences. Ask which companies or job boards I
want to track, add each one through /add-source, run the first collection, and
open the Board. Keep my populated configuration and runtime data private and
untracked.
```

## Start manually

internsHELPer requires Python 3.11 or newer.

```bash
git clone https://github.com/ryanjoyc/internshelper-public.git internshelper
cd internshelper
bash scripts/bootstrap.sh
```

Then add a supported board, collect its postings, and start the dashboard:

```bash
.venv/bin/python -m internshelper.sources add https://boards.greenhouse.io/stripe
.venv/bin/python -m internshelper.run
.venv/bin/python -m internshelper.web
```

Open <http://127.0.0.1:8510>. On macOS, interactive setup can also install the Dock app.

## What it includes

- Connectors for Greenhouse, Lever, Ashby, Workday, Amazon Jobs, and supported GitHub lists.
- A deduplicated local archive, full-text search, company groups, and an application pipeline.
- Repository-local workflows for adding sources, reviewing postings, researching companies, scanning an entire source, and investigating flagged results.
- Optional email digests and hourly macOS scheduling, both disabled by default.

## Private by default

Your profile, selected sources, company groups, application records, raw payloads, logs, and database live in ignored local files. The dashboard binds to `127.0.0.1`. Check `git status` before committing changes.

## Read more

- [Full setup and operations](docs/operator-guide.md)
- [Agent instructions](AGENTS.md)
- [Architecture and command guide](.agents/skills/internshelper-guide/SKILL.md)
- [Supported sources and security boundaries](docs/source-coverage.md)
- [Documentation index](docs/README.md)
- [Roadmap](ROADMAP.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

The project does not currently include a license. Public visibility does not grant permission to copy, modify, or redistribute the project source.
