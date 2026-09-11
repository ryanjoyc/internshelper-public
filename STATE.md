# Project state — internsHELPer

_Last updated: 2026-09-11 · maintained by the project-state skills · keep this to one screen_

## Current focus

- **Public release:** the sanitized-history candidate contains only public examples, technical
  documentation, source, tests, and required third-party notices. Publication awaits approval.
- **Configuration boundary:** `.env`, the role profile, source selection, company index, company
  groups, SQLite data, payloads, logs, and exports are private per-user state. Tracked
  `*.example.*` files define the public starting point.
- **Release verification:** the offline, availability-contract, browser, clean-install, license,
  file-inventory, and full-history secret/privacy checks passed. An actual native WKWebView launch
  remains a documented verification gap.
- **Packaging:** the macOS Dock app remains a local launcher over a checkout and virtual environment;
  standalone distribution is future work.

## Git / VCS status

_Snapshot as of 2026-09-11, after public-release verification._

- **Supported branch:** `main`.
- **Working tree:** the public-release cleanup is committed on `main`; use live `git status` before
  relying on this snapshot.
- **Remote effects:** this candidate has no remote. No repository was created, pushed, or made
  public during preparation.
- **Historical branch:** `archive/phase2-connector-coverage` is archived development history and is
  not a supported release branch.

## Planning docs

| Doc | Path | One-line summary | Status |
|---|---|---|---|
| Docs index | `docs/README.md` | Reading order and documentation lifecycle rules | active |
| Roadmap | `ROADMAP.md` | Forward-looking product direction and deferred decisions | active |
| Readme | `README.md` | Public setup, operation, testing, and privacy guidance | active |
| Codex Cloud | `docs/codex-cloud.md` | Engineering-only Cloud setup and safety boundaries | active |
| Source coverage | `docs/source-coverage.md` | Connector support, limits, and acceptance criteria | reference |
| UI design system | `docs/ui-design-system.md` | Interface principles, components, and verification | reference |
| Availability catalog | `docs/availability-verification-catalog.md` | Generated acceptance cases | approved |
| Availability coverage | `docs/availability-verification-coverage.md` | Generated coverage and contract commands | approved |
| Availability baseline | `docs/availability-current-baseline.md` | Frozen pre-implementation comparison | reference |
| Role-profile template | `config/profile.example.md` | Safe starting point for private fit criteria | reference |
| Third-party notices | `THIRD_PARTY_NOTICES.md` | Vendored component provenance and licenses | reference |
