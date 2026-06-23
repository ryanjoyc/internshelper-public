# internsHELPer — Roadmap

> Status: living document. Last updated 2026-06-21.
> This is a north-star + phased plan, not a commitment to dates.

## North Star

internsHELPer is a **tool you download and own** — a personal, self-hosted
internship/job aggregator. You point it at the sources *you* care about; it
reliably collects, indexes, and shows you **everything** from them; you triage and
apply from one place; and it tracks the applications you've sent.

You should be able to start in five minutes with a few optional default sources,
ignore them entirely if you want, and add your own boards by pasting a link. The
promise we are making — and the one we must never break — is:

> **If you added a source, you will see every relevant job it lists.
> We will never silently lose an opportunity you'd have cared about.**

## Principles

1. **You own it.** Local-first. Your data (SQLite), your source list (`sources.yaml`),
   your machine. No account required, no server we run.
2. **A trust floor that needs no AI.** Deterministic parsers do the extraction; a
   human makes the final call. Accuracy is **identical with or without Claude Code**.
   AI is an *accelerator*, never a *dependency for correctness*.
3. **No silent misses.** Anything fetched is visible. Anything filtered is shown as
   *filtered* (not dropped), explained, and one click from being un-filtered.
4. **One product, progressively enhanced.** Not two forks. The core runs standalone;
   an optional AI-assist layer (Claude Code skill *or* a bring-your-own API key)
   helps with the hard long tail only.
5. **Trust = coverage × transparency, in equal measure.** We expand the boards we
   can parse *and* make exactly what happened during parsing legible to the user.

## The "do we need Claude Code?" answer

No version split. Today, two actions lean on Claude Code skills:

- **Reviewing the queue** (`/review-internships`) — the skill only *drives* the
  decision; the human makes it. This is fully replaceable by an in-app review screen
  with **no loss of accuracy**.
- **Adding a messy source** (`/add-source`) — resolving "add Stripe" or an arbitrary
  careers page to a real board slug. Code can handle most of this deterministically
  (most careers pages embed a Greenhouse/Lever/Ashby widget whose slug is sniffable
  from the page HTML); only the genuine long tail benefits from an agent/LLM.

So: **standalone core for correctness, optional AI-assist for convenience and the
long tail.** Same codebase, same accuracy floor for everyone.

---

## Phase 0 — Harden the floor (close known silent-miss gaps)

*Goal: make the current pipeline honest about what it did, before adding surface area.*

- **Kill the silent display filter.** The dashboard default
  (`require_cs AND require_intern_or_newgrad`) hides un-flagged pending postings.
  Reframe filtered items as a visible, labeled "Filtered (N) — show" bucket rather
  than an invisible drop. (`internshelper/filters.py`, `internshelper/dashboard.py`)
- **Make flood-guard drops visible.** When `title_must_match` rejects a row, count
  it and surface it in the Health tab instead of dropping silently. (`run.py`,
  `store.record_run`)
- **Warn on degenerate parses at add-time.** If a markdown/community list fetch-tests
  to 0 rows, or required columns can't be mapped, warn loudly before writing the
  source. (`sources.py` fetch-test path, `connectors/markdown_list.py`)
- **Source "went quiet" detection.** Flag in Health when a source's fetch count drops
  to zero (or sharply) vs its recent baseline — the classic "the board changed and we
  silently stopped seeing jobs" failure. (`runs` table, Health tab)

## Phase 1 — Standalone core (remove Claude Code as a *requirement*)

*Goal: a general user can add supported sources and review the queue with no agent.*

- **In-app review UI.** Streamlit screen replicating `/review-internships`:
  per-posting match / no_match / notes, reading the archived payload. Wire to the
  existing `internshelper/review.py` CLI — same accuracy, no skill needed.
- **In-app add-source for supported boards.** Paste a Greenhouse/Lever/Ashby/GitHub/
  Markdown URL → `detect_source` → fetch-test → write to `sources.yaml`. The plumbing
  already exists (`sourceurl.py`, `sources.py`); this is a UI over it.
- **Heuristic ATS sniffing (no AI).** Given an arbitrary careers-page URL, fetch the
  HTML and detect an embedded Greenhouse/Lever/Ashby board, then resolve the slug.
  Covers a large share of "arbitrary URL" inputs deterministically.

## Phase 2 — Coverage (parse more of the web reliably)

*Goal: fewer sources hard-fail; the "both, balanced" coverage pillar.*

> **This is the most important phase, and the one that deserves the most effort.**
> Parsing accuracy *is* the product's core promise — "if you added a source, you will
> see every relevant job it lists." Everything downstream (review, apply, track) is
> worthless if capture is lossy or wrong. We must parse and extract from each source
> **accurately and without AI** — accuracy has to be identical with or without a coding
> agent. Treat this phase as the bedrock, not a feature list to rush through.
>
> **Before building, brainstorm an eval loop.** The only way to know we're actually
> parsing accurately is to measure it. Up front, design how we test extraction
> accuracy — curated fixture pages per connector (real, saved HTML/JSON), expected
> extracted output, and a harness that scores parsed-vs-expected (precision/recall on
> roles + per-field correctness). Build this eval loop *alongside* development so we
> track our accuracy continuously and catch regressions, rather than discovering misses
> in production. Nail down the eval design first; don't start the connectors until we
> can measure them.

- **Generic structured-data connector.** Parse `schema.org/JobPosting` JSON-LD, which
  many job pages expose — deterministic and reliable, a strong generic fallback.
- **More ATS connectors.** Workday, SmartRecruiters, iCIMS, etc., each a new module in
  the existing `connectors/` registry pattern (`connectors/base.py`).
- **LinkedIn — set honest expectations.** LinkedIn is ToS-hostile and anti-bot;
  scraping is fragile and risky. Plan it as a **manual import / paste-a-job path**
  (and any sanctioned public endpoints) rather than promising live scraping.
- **Pagination audit.** Defensively confirm each ATS connector returns *all* roles,
  not just a first page.

## Phase 3 — Transparency & trust tooling (the other half of trust)

*Goal: make every parsing decision legible and every miss recoverable.*

- **Per-posting provenance.** Show which connector, the raw payload, what was
  extracted, and a parse-confidence signal.
- **Per-source coverage report.** "Fetched N · shown N · filtered N (why)" plus a diff
  vs last run (new / closed). Turns trust from a promise into something inspectable.
- **Manual overrides everywhere.** Force-include a filtered posting; add a single job
  by URL or paste; correct a misparse.
- **Optional AI-assist layer (BYO Claude Code *or* API key).** Smart add-source for the
  long tail and review *suggestions* — clearly labeled, never auto-deciding. This is
  where Claude Code plugs back in as enhancement, not requirement.

## Phase 4 — Apply & track (close the loop)

*Goal: the "go apply, and we remember it" half of the vision.*

- **Richer application lifecycle.** Extend the existing `applications` table
  (status / notes / applied_date) with stages, deadlines, and follow-up reminders.
- **Closed-since-saved alerts.** Surface existing close-detection into the Tracker so a
  job that vanished after you saved it is flagged, not silently stale.
- **Export.** CSV/JSON of matches and applications for use elsewhere.

---

## Open questions

- **Distribution:** pip package, Docker, or a one-click bundle? Who is the least
  technical user we want to support?
- **Scope:** strictly single-user/local forever, or eventually shareable source packs?
- **AI-assist economics:** where does a BYO API key live, and what does a user pay?
- **LinkedIn stance:** manual import only, or invest in fragile integration?
- **Default sources:** which ship enabled out of the box, and how opinionated are they?
