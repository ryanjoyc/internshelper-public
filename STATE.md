# Project state — internsHELPer

_Last updated: 2026-09-11 · maintained by the project-state skills · keep this to one screen_

## Current focus

- **Public release:** `main` contains the sanitized development history, public examples, technical
  documentation, source, tests, and required third-party notices.
- **Configuration boundary:** `.env`, the role profile, source selection, company index, company
  groups, SQLite data, payloads, logs, and exports are private per-user state. Tracked
  `*.example.*` files define the public starting point.
- **Default experience:** manual collection, availability checks, the dashboard, and the macOS Dock
  app are the primary path. Email digests and launchd scheduling remain available by explicit
  opt-in and default to off.
- **Release verification:** the offline, availability-contract, browser, clean-install, packaging,
  dependency, license, file-inventory, and full-history secret/privacy checks pass. A clean-clone
  server served the Board and the Dock bundle built successfully; direct WKWebView inspection
  remains unavailable without macOS accessibility permission.
- **Packaging:** the macOS Dock app remains a local launcher over a checkout and virtual environment;
  standalone distribution is future work.

## Git / VCS status

_Snapshot as of 2026-09-11, after public-release verification._

- **Supported branch:** `main`.
- **Working tree:** the public-release cleanup is committed on `main`; use live `git status` before
  relying on this snapshot.
- **Publication state:** repository visibility and remotes are checkout-specific; verify them with
  `git remote -v` and the repository host before relying on this snapshot.
- **History:** the phase-two experiments are retained in `main`'s ancestry; no separate release
  branch or tag is required.

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
