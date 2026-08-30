# Set up Codex Cloud

Use Codex Cloud as an engineering and test workspace for internsHELPer. Keep collection, source
research, email, the real SQLite database, and macOS app operations on the user's Mac.

Cloud tasks use temporary SQLite files created by pytest. Do not upload `data/`, `.env`, raw
payloads, or SMTP credentials.

## Prepare the repository

Push the branch you want Cloud to use. A Cloud task can only see commits available through GitHub.
Use one writer per branch: finish or checkpoint a Cloud task before editing the same branch locally.

The repository includes the pstack skills under `.agents/skills`, so Cloud does not depend on
the user's personal Codex plugin cache. `scripts/setup-codex-cloud.sh` installs the remaining tools and
rebuilds the ignored code-review graph for the selected branch.

On the Mac, Codex may list both `engineering-mode` and `pstack-codex:engineering-mode`. They are the
same pinned bundle from two discovery locations. Cloud uses the repository-scoped name.

## Create the environment

1. Open Codex settings and connect the GitHub repository `OWNER/internsHELPer`.
2. Create an environment named `internshelper-engineering`.
3. Pin Python to `3.13`.
4. Add the environment variable `INTERNSHELPER_CLOUD=1`.
5. Add no secrets.
6. Leave agent internet access disabled. Setup scripts still receive package-download access.
7. Paste this setup script into the environment:

   ```bash
   if [ -f scripts/setup-codex-cloud.sh ]; then
     bash scripts/setup-codex-cloud.sh
   else
     python3 -m venv .venv
     .venv/bin/python -m pip install --upgrade pip
     .venv/bin/python -m pip install -e ".[dev,web]"
   fi
   ```

   The fallback lets Codex seed a cache from an older default branch that does not yet contain the
   Cloud script.

8. Set the maintenance script to:

   ```bash
   bash scripts/setup-codex-cloud.sh --maintenance
   ```

9. Reset the environment cache after changing either script, the Python version, or environment
   variables.

Codex runs setup before the agent phase and maintenance after restoring a cached container onto the
selected branch. The setup installs the `ui` test extra, Chromium, and `code-review-graph 2.3.2`.

## Verify a fresh Cloud checkout

Select `codex/package-uncommitted-arc` and start a no-change task with this prompt:

```text
Use $internshelper-engineering and $engineering-mode. Make no code or data changes.
Run bash scripts/verify-codex-cloud.sh and report each check, including expected xfails.
```

The command validates the checked-in pstack bundle, confirms the graph matches the selected branch
and commit, and runs the default, availability-baseline, availability-contract, and browser suites.
It never runs `live_canary`.

After verification, confirm `git status --short` contains no new tracked changes. The ignored graph,
pytest caches, Playwright files, and temporary databases may be recreated inside the Cloud
container.

## Work and resume safely

- Start implementation tasks from a pushed branch or commit.
- Ask Cloud to update `STATE.md` before it hands back a substantial implementation diff.
- Open a PR or otherwise save the Cloud branch to GitHub before switching the same work back to
  the Mac.
- Resume from the Cloud chat or from the pushed checkpoint. Do not rely on the cached container;
  OpenAI may replace it.
- Open the same Cloud chat from a phone to inspect results and send follow-ups while the Mac is off.

## Keep these operations local

Do not run these in the engineering Cloud environment:

- `python -m internshelper.run` or any collection cycle;
- source, company, review, or Board commands that persist user state;
- `live_canary` or live posting investigations;
- email, `.env` scaffolding, launchd installation, Dock app packaging, or native macOS checks.

Run macOS acceptance and any deliberate operation against the user's real database on the Mac after
reviewing the Cloud diff.
