# Availability verification case catalog

This file is generated from `tests/availability/corpus.yaml`. Edit the YAML and run
`.venv/bin/python tests/availability/generate.py`; do not edit this catalog directly.

- Corpus schema: `1`
- Policy version: `availability-policy-v1`
- Approval: `approved` by the user on `2026-08-30`
- Cases: `34`
- Scope: test fixtures and future-behavior contracts only; no production availability logic

## How to read a case

The timeline states what was observed and in what order. The expected result is the Board
contract the user is approving. A replacement candidate never changes the stored URL unless the
case explicitly says confirmation occurred.

## Cases

### `ats_live_initial`

A first-party ATS job page loads normally during initial validation.

- Category: `confirmed_live`
- Provenance: `synthetic` — Minimal first-party success response with a visible job title and application control.
- Source: `first_party_ats` / `greenhouse` (Example employer ATS)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `network` | HTTP 200 / job page | The employer page shows the role details and an enabled application control. (`live_page`) |

| Expected field | Value |
|---|---|
| Availability | `live` |
| Board treatment | `normal` |
| Primary action | Apply |
| Secondary action | None |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: Direct first-party application evidence is enough to keep the ordinary Apply experience without a warning or ranking change.

### `community_checked_before_display`

A community-list row is present and its destination is checked before first display.

- Category: `community_pre_display`
- Provenance: `synthetic` — Community row and destination response are trimmed to the fields needed for ordering the checks.
- Source: `community_list` / `markdown` (Example internship list)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `source` | present | The list contains the employer, role, and destination URL. (`community_presence`) |
| 2 | T+00m | `initial_validation` | `network` | HTTP 200 / job page | The destination succeeds before the posting becomes eligible for first display. (`live_page`) |

| Expected field | Value |
|---|---|
| Availability | `live` |
| Board treatment | `normal` |
| Primary action | Apply |
| Secondary action | None |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: The community row supplies discovery evidence, but the successful destination check is what makes first display safe.

### `unknown_redirect_to_live`

An unfamiliar destination redirects once to a live employer-hosted job page.

- Category: `redirect_recovery`
- Provenance: `synthetic` — Two frozen HTTP steps exercise deterministic redirect following without assuming an ATS vendor.
- Source: `unknown` / `unfamiliar_web` (Unfamiliar careers host)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `network` | HTTP 302 / redirect | The original link redirects to an employer-owned careers host. (`redirect`) |
| 2 | T+00m | `initial_validation` | `network` | HTTP 200 / job page | The final page contains the role and an application control. (`live_page`) |

| Expected field | Value |
|---|---|
| Availability | `live` |
| Board treatment | `normal` |
| Primary action | Apply |
| Secondary action | None |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: A bounded redirect that finishes on a usable employer page is live; unfamiliar hosting alone should not add friction.

### `unusual_title_live_job`

A live employer page uses an unusual title that contains the word closed in a team name.

- Category: `unusual_live_page`
- Provenance: `synthetic` — The title is deliberately awkward so title-only closure heuristics fail this case.
- Source: `first_party_ats` / `lever` (Example employer ATS)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `network` | HTTP 200 / job page | The page says Closed Loop Systems Intern and still exposes an active application form. (`live_page, unusual_title`) |

| Expected field | Value |
|---|---|
| Availability | `live` |
| Board treatment | `normal` |
| Primary action | Apply |
| Secondary action | None |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: The active form and job details outweigh a misleading title token, preventing a brittle text-only false closure.

### `multiple_sources_agree_live`

First-party enumeration, a community row, and the destination all agree that a role is live.

- Category: `corroborated_live`
- Provenance: `synthetic` — Frozen ATS and Markdown snippets represent independent observations of the same stable posting ID.
- Source: `first_party_ats` / `ashby` (Example employer ATS with community mirror)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `source` | present | Complete ATS enumeration includes the posting ID. (`multiple_source_observations`) |
| 2 | T+00m | `initial_validation` | `source` | present | The community list points to the same destination. (`community_presence, multiple_source_observations`) |
| 3 | T+00m | `initial_validation` | `network` | HTTP 200 / job page | The application page is usable. (`live_page`) |

| Expected field | Value |
|---|---|
| Availability | `live` |
| Board treatment | `normal` |
| Primary action | Apply |
| Secondary action | None |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: Independent observations reinforce the usable destination, so no warning, guard, or ranking consequence is warranted.

### `single_404_guarded`

The original first-party link returns one 404 during initial validation.

- Category: `hard_failure_once`
- Provenance: `synthetic` — Minimal employer-hosted 404 response with no explicit role-closure statement.
- Source: `first_party_ats` / `greenhouse` (Example employer ATS)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `network` | HTTP 404 / not found | The posting URL is missing once; no retry or board enumeration has completed yet. (`single_404`) |

| Expected field | Value |
|---|---|
| Availability | `uncertain` |
| Board treatment | `guarded` |
| Primary action | Verify and find application |
| Secondary action | Open Original |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: One hard failure can be transient or caused by a moved posting, so the row stays visible while the known-bad Apply path is guarded.

### `single_410_guarded`

The original first-party link returns one 410 during initial validation.

- Category: `hard_failure_once`
- Provenance: `synthetic` — Minimal HTTP 410 response without corroborating source enumeration.
- Source: `first_party_ats` / `lever` (Example employer ATS)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `network` | HTTP 410 / gone | The server reports Gone once, before the scheduled consistency check. (`single_410`) |

| Expected field | Value |
|---|---|
| Availability | `uncertain` |
| Board treatment | `guarded` |
| Primary action | Verify and find application |
| Secondary action | Open Original |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: A single 410 is stronger than a timeout but still receives the same one-observation safety margin before closure.

### `repeated_404_closed`

The same posting returns 404 during initial validation and a later scheduled retry.

- Category: `confirmed_closed`
- Provenance: `synthetic` — Identical frozen responses model two independent checks separated in time.
- Source: `first_party_ats` / `greenhouse` (Example employer ATS)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `network` | HTTP 404 / not found | The first check records a hard failure but does not close the role. (`single_404`) |
| 2 | T+60m | `scheduled_retry` | `network` | HTTP 404 / not found | The scheduled consistency check returns the same hard failure. (`repeated_404`) |

| Expected field | Value |
|---|---|
| Availability | `closed` |
| Board treatment | `archived` |
| Primary action | None |
| Secondary action | None |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: Two consistent hard failures across separate checks meet the closure threshold for an ordinary, unprotected posting.

### `repeated_410_interested_visible`

An interested posting returns 410 on two separate checks.

- Category: `protected_closed`
- Provenance: `synthetic` — The network evidence matches the repeated-hard-failure case while user state is protected.
- Source: `first_party_ats` / `lever` (Example employer ATS)
- User state: `interested`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `network` | HTTP 410 / gone | The initial check records Gone. (`single_410`) |
| 2 | T+60m | `scheduled_retry` | `network` | HTTP 410 / gone | The scheduled retry returns the same result. (`repeated_410`) |

| Expected field | Value |
|---|---|
| Availability | `closed` |
| Board treatment | `visible_closed` |
| Primary action | None |
| Secondary action | Open Original |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: The evidence confirms closure, but the user's saved interest is durable state and keeps the closed row visible for context.

### `employer_closure_applied_visible`

An applied posting displays specific employer-authored text saying the role is no longer accepting applications.

- Category: `protected_closed`
- Provenance: `synthetic` — The excerpt contains a role-specific employer closure sentence rather than a generic site error.
- Source: `first_party_ats` / `workday` (Example employer careers site)
- User state: `applied`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `network` | HTTP 200 / employer closure | The employer page names the role and states that it no longer accepts applications. (`employer_closure_text`) |

| Expected field | Value |
|---|---|
| Availability | `closed` |
| Board treatment | `visible_closed` |
| Primary action | None |
| Secondary action | Open Original |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: Specific first-party closure language is decisive immediately, while the existing application record keeps the posting visible.

### `generic_error_text_uncertain`

The employer host returns a generic error page without role-specific closure language.

- Category: `ambiguous_error`
- Provenance: `synthetic` — Generic request-failure copy is frozen separately from the employer-closure fixture.
- Source: `first_party_ats` / `workday` (Example employer careers site)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `network` | HTTP 200 / generic error page | The page says the request could not be completed and gives no role-specific status. (`generic_error_text`) |

| Expected field | Value |
|---|---|
| Availability | `uncertain` |
| Board treatment | `warned` |
| Primary action | Verify and find application |
| Secondary action | Open Original |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: Generic error copy describes the site's state, not the role's state, so the Board offers recovery without declaring closure.

### `ats_absence_once_uncertain`

One complete first-party ATS enumeration omits a previously known posting.

- Category: `source_absence`
- Provenance: `synthetic` — A complete successful enumeration is distinct from an empty, partial, or failed fetch.
- Source: `first_party_ats` / `ashby` (Example employer ATS)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `source` | absent | The source fetch completed and other roles were present, but this stable posting ID was absent. (`single_ats_absence`) |

| Expected field | Value |
|---|---|
| Availability | `uncertain` |
| Board treatment | `warned` |
| Primary action | Apply |
| Secondary action | Verify and find application |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: One complete absence is meaningful but can reflect source lag or republishing, so it warns without closing or guarding the stored link.

### `ats_absence_twice_closed`

Two complete first-party ATS enumerations omit the same previously known posting.

- Category: `confirmed_closed`
- Provenance: `synthetic` — Two successful source snapshots model the absence threshold without any network ambiguity.
- Source: `first_party_ats` / `ashby` (Example employer ATS)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `source` | absent | The first complete enumeration omits the posting. (`single_ats_absence`) |
| 2 | T+60m | `scheduled_retry` | `source` | absent | A later complete enumeration omits the same posting again. (`repeated_ats_absence`) |

| Expected field | Value |
|---|---|
| Availability | `closed` |
| Board treatment | `archived` |
| Primary action | None |
| Secondary action | None |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: Two complete first-party absences across separate observations meet the closure threshold for an ordinary posting.

### `community_removed_but_destination_live`

A community list removes a row while the original employer application page remains live.

- Category: `community_removal`
- Provenance: `synthetic` — The removed community snapshot and live employer page are independent frozen fixtures.
- Source: `community_list` / `markdown` (Example internship list)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `source` | removed | The latest list no longer contains the row. (`community_removal`) |
| 2 | T+00m | `initial_validation` | `network` | HTTP 200 / job page | The employer destination still accepts applications. (`live_page`) |

| Expected field | Value |
|---|---|
| Availability | `live` |
| Board treatment | `normal` |
| Primary action | Apply |
| Secondary action | None |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: Community removal can reflect list maintenance; the usable first-party destination is stronger evidence that the role remains live.

### `community_presence_destination_timeout`

A community row is present, but its destination times out during the required pre-display check.

- Category: `community_unverified`
- Provenance: `synthetic` — The fixture pair distinguishes list presence from destination availability.
- Source: `community_list` / `markdown` (Example internship list)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `source` | present | The list advertises the posting. (`community_presence`) |
| 2 | T+00m | `initial_validation` | `network` | timeout | The destination provides no user-visible response before the check deadline. (`timeout`) |

| Expected field | Value |
|---|---|
| Availability | `uncertain` |
| Board treatment | `warned` |
| Primary action | Verify and find application |
| Secondary action | Open Original |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: Community presence cannot override an unverified destination, so the initial Board treatment favors recovery rather than a blind Apply action.

### `first_party_timeout_uncertain`

A first-party application page times out on its first check.

- Category: `transport_failure`
- Provenance: `synthetic` — A scripted transport timeout contains no role-status evidence.
- Source: `first_party_ats` / `greenhouse` (Example employer ATS)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `network` | timeout | The request deadline expires without an HTTP response. (`timeout`) |

| Expected field | Value |
|---|---|
| Availability | `uncertain` |
| Board treatment | `warned` |
| Primary action | Verify and find application |
| Secondary action | Open Original |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: A timeout is an outage observation rather than closure evidence, so the recovery action leads while availability stays uncertain.

### `dns_failure_uncertain`

DNS resolution fails for an unfamiliar job host.

- Category: `transport_failure`
- Provenance: `synthetic` — A scripted name-resolution failure has no fabricated HTTP response.
- Source: `unknown` / `unfamiliar_web` (Unfamiliar careers host)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `network` | dns error | The hostname cannot be resolved during the check. (`dns_failure`) |

| Expected field | Value |
|---|---|
| Availability | `uncertain` |
| Board treatment | `warned` |
| Primary action | Verify and find application |
| Secondary action | Open Original |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: DNS failure says the host is unreachable now, not that the employer closed the role, so the result remains uncertain.

### `connection_failure_uncertain`

A connection is refused before the job host returns an HTTP response.

- Category: `transport_failure`
- Provenance: `synthetic` — A deterministic connection failure is separate from timeout and DNS cases.
- Source: `first_party_ats` / `custom_ats` (Example employer careers host)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `network` | connection error | The remote endpoint refuses the connection before any page is served. (`connection_failure`) |

| Expected field | Value |
|---|---|
| Availability | `uncertain` |
| Board treatment | `warned` |
| Primary action | Verify and find application |
| Secondary action | Open Original |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: A refused connection is a host-level outage with no role-specific meaning, so it triggers recovery rather than closure.

### `automation_403_keeps_apply`

The automated checker receives 403 from a destination that may still work in the user's browser.

- Category: `checker_barrier`
- Provenance: `synthetic` — The response identifies automated-access denial without employer closure text.
- Source: `first_party_ats` / `custom_ats` (Bot-protected employer site)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `network` | HTTP 403 / forbidden to checker | The host rejects the checker user agent and does not claim the role is closed. (`http_403`) |

| Expected field | Value |
|---|---|
| Availability | `uncertain` |
| Board treatment | `normal` |
| Primary action | Apply |
| Secondary action | Verify and find application |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: The failure is specific to automation, so the user should still get the direct Apply path before an optional deeper verification.

### `anti_bot_keeps_apply`

The checker receives a challenge page that asks a browser to prove it is human.

- Category: `checker_barrier`
- Provenance: `synthetic` — The challenge excerpt contains no posting-status claim.
- Source: `first_party_ats` / `custom_ats` (Challenge-protected employer site)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `network` | HTTP 200 / anti bot challenge | The response asks for a human/browser challenge instead of serving job content. (`anti_bot`) |

| Expected field | Value |
|---|---|
| Availability | `uncertain` |
| Board treatment | `normal` |
| Primary action | Apply |
| Secondary action | Verify and find application |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: A human browser may pass the challenge, so guarding the direct action would turn checker weakness into user-facing friction.

### `javascript_required_keeps_apply`

The checker receives only a JavaScript-required application shell.

- Category: `checker_barrier`
- Provenance: `synthetic` — The frozen shell states that JavaScript is required and contains no role status.
- Source: `first_party_ats` / `custom_ats` (JavaScript application site)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `network` | HTTP 200 / javascript shell | Static fetching cannot render the application, but a normal browser can execute it. (`javascript_required`) |

| Expected field | Value |
|---|---|
| Availability | `uncertain` |
| Board treatment | `normal` |
| Primary action | Apply |
| Secondary action | Verify and find application |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: Missing JavaScript execution is a checker limitation, so Apply remains the fastest way for the user to test the real browser path.

### `login_required_keeps_apply`

The destination asks the visitor to sign in before showing the application.

- Category: `checker_barrier`
- Provenance: `synthetic` — The login page is sanitized and carries no claim that the underlying job is closed.
- Source: `first_party_ats` / `custom_ats` (Login-gated employer site)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `network` | HTTP 200 / login gate | The page requires an interactive user session before the role can be inspected. (`login_required`) |

| Expected field | Value |
|---|---|
| Availability | `uncertain` |
| Board treatment | `normal` |
| Primary action | Apply |
| Secondary action | Verify and find application |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: A login gate is actionable by the user and supplies no closure evidence, so Apply stays primary and the checker remains cautious.

### `rate_limited_429_outage`

The application endpoint returns 429 and a retry-after response.

- Category: `user_facing_outage`
- Provenance: `synthetic` — The rate-limit response is frozen without interpreting it as a role decision.
- Source: `first_party_ats` / `custom_ats` (Rate-limited employer site)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `network` | HTTP 429 / rate limited | The endpoint asks the visitor to retry later and gives no posting status. (`http_429`) |

| Expected field | Value |
|---|---|
| Availability | `uncertain` |
| Board treatment | `warned` |
| Primary action | Verify and find application |
| Secondary action | Open Original |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: Rate limiting blocks the current user path but says nothing about closure, so verification leads and the original remains available.

### `service_5xx_outage`

The employer application endpoint returns a server error.

- Category: `user_facing_outage`
- Provenance: `synthetic` — A minimal 503 response represents the 5xx family without vendor-specific copy.
- Source: `first_party_ats` / `custom_ats` (Employer careers service)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `network` | HTTP 503 / service unavailable | The service is unavailable and provides no role-specific information. (`http_5xx`) |

| Expected field | Value |
|---|---|
| Availability | `uncertain` |
| Board treatment | `warned` |
| Primary action | Verify and find application |
| Secondary action | Open Original |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: Server failure is an outage, not a hiring decision, so the Board exposes recovery while preserving an uncertain result.

### `maintenance_page_outage`

The first-party careers site shows a scheduled maintenance page.

- Category: `user_facing_outage`
- Provenance: `synthetic` — Generic maintenance copy is separated from the sanitized Workday production pattern.
- Source: `first_party_ats` / `custom_ats` (Employer careers service)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `network` | HTTP 200 / maintenance page | The host announces temporary maintenance and does not render the posting. (`maintenance`) |

| Expected field | Value |
|---|---|
| Availability | `uncertain` |
| Board treatment | `warned` |
| Primary action | Verify and find application |
| Secondary action | Open Original |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: Explicit maintenance describes temporary site availability, so closing or hiding the role would overstate the evidence.

### `redirect_loop_outage`

The application URL redirects between two pages until the redirect limit is reached.

- Category: `user_facing_outage`
- Provenance: `synthetic` — A deterministic redirect-loop script avoids relying on an HTTP client's vendor-specific exception text.
- Source: `unknown` / `unfamiliar_web` (Unfamiliar careers host)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `network` | redirect loop | The destination alternates between two URLs and never reaches job content. (`redirect_loop`) |

| Expected field | Value |
|---|---|
| Availability | `uncertain` |
| Board treatment | `warned` |
| Primary action | Verify and find application |
| Secondary action | Open Original |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: A redirect loop makes the user path unusable but does not reveal whether the role closed, so recovery leads without closure.

### `trustworthy_evidence_conflicts`

Repeated first-party absence conflicts with a second trustworthy employer page that is live.

- Category: `conflicting_evidence`
- Provenance: `synthetic` — The case combines complete source snapshots with a separate employer-hosted application response.
- Source: `unknown` / `multi_source` (Conflicting employer sources)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `source` | absent | One complete employer ATS omits the old stable ID. (`single_ats_absence, multiple_source_observations`) |
| 2 | T+60m | `scheduled_retry` | `source` | absent | The old ATS omits the stable ID again. (`repeated_ats_absence`) |
| 3 | T+60m | `scheduled_retry` | `network` | HTTP 200 / job page | A second employer-owned page shows the same role and accepts applications. (`live_page, conflicting_evidence, multiple_source_observations`) |

| Expected field | Value |
|---|---|
| Availability | `uncertain` |
| Board treatment | `warned` |
| Primary action | Apply |
| Secondary action | Verify and find application |
| Investigation result | `not_run` |
| Investigation progress | None |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: Neither trustworthy source can be discarded automatically, so the conflict stays visible and uncertain instead of forcing closure.

### `original_link_recovered`

A user-triggered investigation retries an earlier timeout and recovers the original application page.

- Category: `investigation_recovery`
- Provenance: `synthetic` — The original URL is unchanged; the timeline records failure, user investigation, and recovery.
- Source: `first_party_ats` / `greenhouse` (Example employer ATS)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`, `investigation`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `network` | timeout | Initial validation times out. (`timeout`) |
| 2 | T+05m | `user_investigation` | `network` | HTTP 200 / job page | The investigator retry reaches the original live application page. (`live_page`) |
| 3 | T+05m | `user_investigation` | `investigator` | original recovered | The investigator reports successful recovery without proposing a URL change. (`original_recovered`) |

| Expected field | Value |
|---|---|
| Availability | `live` |
| Board treatment | `normal` |
| Primary action | Apply |
| Secondary action | None |
| Investigation result | `original_recovered` |
| Investigation progress | queued → check original → retry original → complete |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: Direct recovery of the original link removes the warning and restores Apply without touching the stored URL or rank.

### `replacement_requires_confirmation`

Investigation finds the original missing and discovers a plausible first-party replacement.

- Category: `investigation_replacement`
- Provenance: `synthetic` — Stable IDs and URLs use example.test while preserving the absence-to-replacement sequence.
- Source: `first_party_ats` / `greenhouse` (Example employer ATS)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`, `investigation`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `user_investigation` | `network` | HTTP 404 / not found | The stored URL fails during investigation. (`single_404`) |
| 2 | T+00m | `user_investigation` | `source` | absent | Complete first-party enumeration does not contain the original ID. (`single_ats_absence`) |
| 3 | T+00m | `user_investigation` | `source` | present | A title, company, and location match appears under a new stable ID. (`replacement_url, multiple_source_observations`) |
| 4 | T+00m | `user_investigation` | `investigator` | replacement found | The investigator offers the candidate and waits for the user to confirm it. (`replacement_discovered`) |

| Expected field | Value |
|---|---|
| Availability | `uncertain` |
| Board treatment | `guarded` |
| Primary action | Confirm replacement |
| Secondary action | Open Original |
| Investigation result | `replacement_found` |
| Investigation progress | queued → check original → enumerate first party → search replacement → review finding → await user confirmation |
| Replacement behavior | `offer_for_confirmation` |
| Replacement candidate | https://jobs.example.test/roles/replacement |
| Replacement confirmation required | yes |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: The candidate may represent a repost or a different role, so the user sees the evidence and the stored URL remains unchanged until confirmation.

### `investigation_confirms_closure`

A user-triggered investigation repeats a hard failure and confirms complete first-party absence.

- Category: `investigation_closure`
- Provenance: `synthetic` — The investigator finding summarizes only evidence already present in the frozen timeline.
- Source: `first_party_ats` / `lever` (Example employer ATS)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`, `investigation`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `user_investigation` | `network` | HTTP 410 / gone | The investigator sees the first hard failure. (`single_410`) |
| 2 | T+01m | `user_investigation` | `network` | HTTP 410 / gone | A bounded retry returns the same hard failure. (`repeated_410`) |
| 3 | T+01m | `user_investigation` | `source` | absent | Complete first-party enumeration also omits the posting. (`single_ats_absence`) |
| 4 | T+01m | `user_investigation` | `investigator` | closure confirmed | The investigator reports corroborated closure and no replacement. (`closure_confirmed`) |

| Expected field | Value |
|---|---|
| Availability | `closed` |
| Board treatment | `archived` |
| Primary action | None |
| Secondary action | None |
| Investigation result | `closure_confirmed` |
| Investigation progress | queued → check original → retry original → enumerate first party → complete |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: Consistent hard failures plus complete first-party absence give the investigation enough evidence to archive an ordinary posting.

### `investigation_remains_unresolved`

A user-triggered investigation encounters transport failures and a failed source enumeration.

- Category: `investigation_unresolved`
- Provenance: `synthetic` — No event invents a posting status when every evidence channel is unavailable.
- Source: `unknown` / `unfamiliar_web` (Unfamiliar careers host)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`, `investigation`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `user_investigation` | `network` | timeout | The original URL times out. (`timeout`) |
| 2 | T+01m | `user_investigation` | `network` | dns error | The bounded retry cannot resolve the host. (`dns_failure`) |
| 3 | T+01m | `user_investigation` | `source` | source error | No authoritative enumeration is available for the unfamiliar source. (`multiple_source_observations`) |
| 4 | T+01m | `user_investigation` | `investigator` | unresolved | The investigator reports the evidence gap without guessing closure. (`investigation_unresolved`) |

| Expected field | Value |
|---|---|
| Availability | `uncertain` |
| Board treatment | `warned` |
| Primary action | Verify and find application |
| Secondary action | Open Original |
| Investigation result | `unresolved` |
| Investigation progress | queued → check original → retry original → compare sources → complete |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: Every path failed to produce role-status evidence, so the honest result is unresolved and visible with recovery actions.

### `point72_cubist_sanitized`

A community-listed Point72/Cubist role has a dead stored link while a plausible employer-hosted repost exists.

- Category: `known_real_failure`
- Provenance: `sanitized_real` — Frozen from the Point72/Cubist failure family; requisition IDs, dates, and response metadata were replaced.
- Source: `community_list` / `markdown` (Sanitized community list)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`, `investigation`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `user_investigation` | `source` | present | The list names Point72/Cubist and retains the old application URL. (`community_presence, multiple_source_observations`) |
| 2 | T+00m | `user_investigation` | `network` | HTTP 404 / not found | The stored destination no longer resolves to a posting. (`single_404`) |
| 3 | T+01m | `user_investigation` | `source` | present | Employer search exposes a similar Cubist-branded role under a new sanitized URL. (`replacement_url, conflicting_evidence, multiple_source_observations`) |
| 4 | T+01m | `user_investigation` | `investigator` | replacement found | Similarity is high enough to offer a candidate, not high enough to rewrite stored state. (`replacement_discovered`) |

| Expected field | Value |
|---|---|
| Availability | `uncertain` |
| Board treatment | `guarded` |
| Primary action | Confirm replacement |
| Secondary action | Open Original |
| Investigation result | `replacement_found` |
| Investigation progress | queued → check original → enumerate first party → compare sources → review finding → await user confirmation |
| Replacement behavior | `offer_for_confirmation` |
| Replacement candidate | https://jobs.example.test/point72/cubist-replacement |
| Replacement confirmation required | yes |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: Brand and requisition ambiguity make automatic replacement unsafe; the user gets the candidate evidence while the original record stays intact.

### `voloridge_sanitized`

A Voloridge community link fails while another list points to a plausible posting on a different ATS host.

- Category: `known_real_failure`
- Provenance: `sanitized_real` — Frozen from observed Greenhouse-versus-HiringThing link drift; identifiers and dates were replaced.
- Source: `community_list` / `markdown` (Sanitized Summer 2027 lists)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`, `investigation`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `user_investigation` | `source` | present | One list retains a sanitized Greenhouse destination. (`community_presence, multiple_source_observations`) |
| 2 | T+00m | `user_investigation` | `network` | HTTP 404 / not found | The stored Greenhouse-style URL returns a hard failure. (`single_404`) |
| 3 | T+01m | `user_investigation` | `source` | present | A second list names a similar role on a sanitized HiringThing-style URL. (`replacement_url, conflicting_evidence, multiple_source_observations`) |
| 4 | T+01m | `user_investigation` | `investigator` | replacement found | The investigator offers the cross-host candidate for the user's confirmation. (`replacement_discovered`) |

| Expected field | Value |
|---|---|
| Availability | `uncertain` |
| Board treatment | `guarded` |
| Primary action | Confirm replacement |
| Secondary action | Open Original |
| Investigation result | `replacement_found` |
| Investigation progress | queued → check original → compare sources → search replacement → review finding → await user confirmation |
| Replacement behavior | `offer_for_confirmation` |
| Replacement candidate | https://jobs.example.test/voloridge/replacement |
| Replacement confirmation required | yes |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: Cross-list and cross-host evidence suggests a moved role but cannot prove identity, so replacement remains an explicit user decision.

### `workday_maintenance_sanitized`

A Workday job route shows maintenance while the board API also fails during retry.

- Category: `known_real_failure`
- Provenance: `sanitized_real` — Frozen from a Workday maintenance failure family; tenant, site, requisition, and timestamps were replaced.
- Source: `first_party_ats` / `workday` (Sanitized Workday employer)
- User state: `ordinary`
- Contract layers: `policy`, `network`, `board`, `investigation`

| # | When | Stage | Observer | Result | Evidence |
|---:|---|---|---|---|---|
| 1 | T+00m | `initial_validation` | `network` | HTTP 200 / maintenance page | The job route displays vendor maintenance copy instead of role-specific content. (`maintenance, generic_error_text`) |
| 2 | T+30m | `scheduled_retry` | `source` | HTTP 503 / source error | The CXS enumeration is unavailable, so absence cannot be measured safely. (`http_5xx`) |
| 3 | T+31m | `user_investigation` | `investigator` | unresolved | The investigation reports a vendor outage and no trustworthy role-status evidence. (`investigation_unresolved`) |

| Expected field | Value |
|---|---|
| Availability | `uncertain` |
| Board treatment | `warned` |
| Primary action | Verify and find application |
| Secondary action | Open Original |
| Investigation result | `unresolved` |
| Investigation progress | queued → check original → enumerate first party → retry original → complete |
| Replacement behavior | `none` |
| Replacement candidate | None |
| Replacement confirmation required | no |
| Stored URL after this case | `original` |
| Ranking effect | `none` |

Why: Both Workday surfaces are unhealthy, so treating missing content as closure would convert vendor downtime into a false hiring decision.
