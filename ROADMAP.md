# internsHELPer roadmap

_Last updated: 2026-09-10 · forward-looking product direction, not a release log_

For current runtime and Git status, read `STATE.md`. For the implementation map, use the
`internshelper-guide` skill. Completed designs and implementation plans live in Git history rather
than the active docs tree.

## North star

internsHELPer is a local-first internship aggregator that collects broadly, organizes by company,
and tracks applications without silently hiding opportunities.

The core promise is:

> If a configured source exposes a relevant posting, internsHELPer should collect it or surface a
> clear, inspectable reason why it could not.

## Product principles

1. **Broad collection, explicit curation.** Source guards prevent obvious floods, but company
   priority and posting decisions remain human-controlled.
2. **Company-first browsing.** Top targets, Known companies, Worth discovering, and neutral
   Unclassified are independent of role-level rank scores.
3. **No opaque approval gate.** Every broadly collected posting reaches the Board unless it is a
   confirmed duplicate, explicitly dismissed, flagged for investigation, or in the application
   pipeline.
4. **Evidence before automation.** Discovery suggestions require a reason and evidence; the app
   never promotes a company into Worth discovering without the user's approval.
5. **Local ownership.** SQLite, raw payloads, source configuration, and application state stay on
   the user's machine. Optional email and agent workflows enhance the product but are not required
   to browse collected postings.

## Shipped baseline

- Deterministic connectors for Greenhouse, Lever, Ashby, Workday CXS, Amazon Jobs, structured
  GitHub lists, and Markdown job tables.
- Per-source title guards, close detection, run history, quiet/error health signals, raw-payload
  capture, and raise-on-partial-fetch protection where a partial result could close live jobs.
- A local FastAPI/HTMX Board with one expandable card per company, newest-first roles, application
  lanes, archive/search, dismissed and flagged views, and reversible duplicate review.
- Independent company systems: `companies.yaml` resolves companies to boards;
  `company-groups.yaml` controls Board priority and evidence-backed discovery proposals.
- A Top-target email digest with an exactly-once `notified_at` watermark.
- A relocation-safe macOS Dock bundle that launches the local web app from the current checkout.
- Evidence-based posting availability: pre-display destination validation, conservative retries,
  saved/applied closed-history visibility, explicit replacement confirmation, and a user-triggered
  investigation flow that cannot affect ranking.
- Repository-local onboarding and task skills for source addition, company resolution, posting
  review, deep source verification, flagged-posting investigation, and project-state maintenance.
- Public configuration templates with ignored per-user source, company, profile, environment, and
  application data.

## Active priorities

### 1. Calibrate Worth discovering

- Research a representative batch of Unclassified companies.
- Record concise proposals with evidence; never approve them automatically.
- Review approve/reject decisions and tighten the research rubric before scaling up.
- Keep role relevance broad: company quality is the grouping question; the user chooses among the
  company's postings.

### 2. Strengthen source reliability and coverage

- Build a repeatable connector evaluation set from frozen real responses: role recall, field
  correctness, pagination completeness, and close-detection safety.
- Add generic `schema.org/JobPosting` JSON-LD as a deterministic fallback.
- Prioritize high-value unsupported ATSes such as SmartRecruiters and iCIMS based on actual target
  companies, not connector count alone.
- Keep LinkedIn and similar anti-bot aggregators out of live scraping; prefer manual import or
  supported first-party boards.
- Track current support boundaries in `docs/source-coverage.md`.

### 3. Make collection decisions more inspectable

- Show per-posting provenance and a concise extraction summary.
- Expand per-source reporting to fetched, accepted, filtered, new, closed, and duplicate counts.
- Add safe manual overrides for a filtered posting or a one-off job URL.
- Preserve reversibility for every curation action.

### 4. Improve application tracking

- Add deadlines and follow-up reminders without turning the Board into a CRM.
- Export applications and visible postings to CSV/JSON.

### 5. Package for non-technical distribution

The current Dock app is a launcher over this checkout and its virtual environment. Do not present
it as a distributable standalone application until this phase is complete.

- Bundle Python, dependencies, templates, and static assets into a self-contained app.
- Move runtime data and mutable config into per-user application-support directories.
- Provide in-app first-run setup and scheduling.
- Sign and notarize the macOS build; add a separate Windows packaging path only if worthwhile.

## Operational boundaries

Scheduling, SMTP configuration, and native app installation are per-machine operations. Keep them
out of unrelated product changes, and verify each one on the machine where it will run.

## Deferred decisions

- Which unsupported ATS produces the most valuable next coverage?
- Should manual single-job import precede another full connector?
- How much application-reminder complexity is genuinely useful?
- When is a standalone signed build worth the maintenance cost?
