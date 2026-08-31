# Source coverage and connector boundaries

_Last updated: 2026-08-31 · implementation reference, not a live freshness report_

For the configured source list, use:

```bash
.venv/bin/python -m internshelper.sources list
```

For operational freshness, inspect the Health page or `data/internshelper.db`; counts and last-run
timestamps do not belong in this document.

## Current configured footprint

`config/sources.yaml` currently validates with 22 entries:

| Type | Configured sources |
|---|---:|
| Greenhouse | 9 |
| Workday | 5 |
| Markdown lists | 4 |
| Ashby | 2 |
| Lever | 1 |
| Amazon Jobs | 1 |

This is a point-in-time configuration summary. The file and `sources list` CLI are authoritative.

## Supported connectors

| Connector | Input and guarantees | Important boundary |
|---|---|---|
| Greenhouse | Public board API by token; full descriptions | Company boards are the tracking unit; Greenhouse itself is not an aggregator. |
| Lever | Public postings API by token | Tracks public board postings only. |
| Ashby | Public job-board API by organization | Keeps public `isListed` postings. |
| Workday CXS | `*.myworkdayjobs.com` board URL with offset pagination | Large tenants should use server-side `search`; incomplete pagination raises, while malformed/overlapping results retain valid rows but cannot drive absence or close detection. |
| Amazon Jobs | US search API by query, paginated | The query is broad; use `title_must_match` to keep the collected slice intentional. Counts, normalized rows, and stable IDs must prove a complete enumeration. |
| GitHub structured list | Simplify-style `listings.json` | Schema-specific, not arbitrary repository JSON. |
| Markdown list | GitHub-flavored pipe tables | Auto-detects common columns; supports overrides and `company: '@heading'` for firm-per-section lists. |

Every connector returns normalized `Posting`s in a `FetchResult` with an explicit completeness
claim. Per-source title guards run after fetching and their dropped counts are recorded. Valid
rows from partial results are stored and destination-checked, but only complete, non-empty
enumerations drive source-absence or close detection.

## Adding coverage

Prefer the narrowest reliable first-party board:

1. Use `/add-source` for a company name, careers page, job link, or ambiguous board URL.
2. Use `.venv/bin/python -m internshelper.sources add <url>` for a clean supported board URL.
3. Use the Companies resolver when starting from the approved-company index; the user approves the
   proposed board before it enters `sources.yaml`.
4. Fetch-test before writing. A zero count or structural warning requires investigation, not an
   automatic `--yes`.

Company priority is separate from source coverage. Adding a board does not make its company a Top
target or Worth discovering; those decisions live in `config/company-groups.yaml`.

## Known unsupported or deliberately constrained sources

| Source family | Current stance |
|---|---|
| SmartRecruiters, iCIMS, Taleo | No first-class connector yet. Add only after a target-company need and fixture-backed connector work. |
| Workday `*.myworkdaysite.com` variants | Not assumed compatible with the current CXS URL parser; investigate per tenant. |
| Generic `schema.org/JobPosting` pages | Planned deterministic fallback, not implemented. |
| LinkedIn, Jobright, similar aggregators | No supported live scraper. Anti-bot and terms-of-service risk make manual import preferable. |
| Single standalone job page | No general manual-import path yet; use the underlying supported company board where possible. |

## Connector acceptance checklist

A new connector is not complete until it has:

- Frozen real-response fixtures with no network dependency in tests.
- Pagination tests that prove the final page is included.
- Stable posting IDs and canonical apply URLs.
- Field assertions for company, title, location, description, and posting date where available.
- A safe failure mode: incomplete results must not trigger false closures.
- Partial-result coverage proving valid new rows are retained while omitted rows accumulate no
  absence evidence.
- URL detection and, when useful, careers-page sniffing.
- `sources add/test/list/remove` coverage and an entry in the `internshelper-guide` skill.

## Next candidates

Prioritize by target-company value rather than by popularity:

1. A connector evaluation harness measuring role recall and field correctness across current ATSes.
2. Generic JSON-LD `JobPosting` fallback for otherwise unsupported public careers pages.
3. SmartRecruiters or iCIMS, selected only after checking which Unclassified/target companies it
   would unlock.
