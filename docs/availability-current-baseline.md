# Current availability implementation baseline

This report compares the approved availability corpus with the implementation that
existed at production-code revision `a8abdc8`. It is generated
from `tests/availability/current-baseline.json`; do not edit it directly.

- Run date: `2026-08-30`
- Approved policy: `availability-policy-v1`
- Cases: `34`
- Pass: **0**
- Mismatch: **29**
- Unsupported: **5**
- Database safety: every case ran in a fresh temporary SQLite database

## What the labels mean

- **Pass:** Current code consumes the relevant evidence and matches every approved semantic field.
- **Mismatch:** At least one user-visible or persisted semantic field differs from the approved result.
- **Unsupported:** Outputs happen to match, but current code does not consume a capability required to establish them.

The current Board's `Open original posting` button is mapped to the contract's
semantic `apply` action because both are the direct destination path. Current code
does not vary that link based on availability.

## Case results

| Case | Result | Approved availability/treatment/action | Current availability/treatment/action | Detail |
|---|---|---|---|---|
| `ats_live_initial` | **unsupported** | `live/normal/apply` | `live/normal/apply` | missing: destination_validation<br>ignored timeline steps: 1 |
| `community_checked_before_display` | **unsupported** | `live/normal/apply` | `live/normal/apply` | missing: community_pre_display_validation, cross_source_reconciliation, destination_validation<br>ignored timeline steps: 2 |
| `unknown_redirect_to_live` | **unsupported** | `live/normal/apply` | `live/normal/apply` | missing: destination_validation<br>ignored timeline steps: 1, 2 |
| `unusual_title_live_job` | **unsupported** | `live/normal/apply` | `live/normal/apply` | missing: content_aware_closure, destination_validation<br>ignored timeline steps: 1 |
| `multiple_sources_agree_live` | **unsupported** | `live/normal/apply` | `live/normal/apply` | missing: cross_source_reconciliation, destination_validation<br>ignored timeline steps: 3 |
| `single_404_guarded` | **mismatch** | `uncertain/guarded/verify` | `live/normal/apply` | differs: availability, board_treatment, primary_action, secondary_action<br>missing: availability_board_treatments, destination_validation, tri_state_availability, verify_action<br>ignored timeline steps: 1 |
| `single_410_guarded` | **mismatch** | `uncertain/guarded/verify` | `live/normal/apply` | differs: availability, board_treatment, primary_action, secondary_action<br>missing: availability_board_treatments, destination_validation, tri_state_availability, verify_action<br>ignored timeline steps: 1 |
| `repeated_404_closed` | **mismatch** | `closed/archived/none` | `live/normal/apply` | differs: availability, board_treatment, primary_action<br>missing: destination_validation, evidence_history_and_retries<br>ignored timeline steps: 1, 2 |
| `repeated_410_interested_visible` | **mismatch** | `closed/visible_closed/none` | `live/normal/apply` | differs: availability, board_treatment, primary_action, secondary_action<br>missing: destination_validation, evidence_history_and_retries<br>ignored timeline steps: 1, 2 |
| `employer_closure_applied_visible` | **mismatch** | `closed/visible_closed/none` | `live/normal/apply` | differs: availability, board_treatment, primary_action, secondary_action<br>missing: content_aware_closure, destination_validation<br>ignored timeline steps: 1 |
| `generic_error_text_uncertain` | **mismatch** | `uncertain/warned/verify` | `live/normal/apply` | differs: availability, board_treatment, primary_action, secondary_action<br>missing: availability_board_treatments, content_aware_closure, destination_validation, tri_state_availability, verify_action<br>ignored timeline steps: 1 |
| `ats_absence_once_uncertain` | **mismatch** | `uncertain/warned/apply` | `closed/visible_closed/apply` | differs: availability, board_treatment, secondary_action<br>missing: availability_board_treatments, tri_state_availability |
| `ats_absence_twice_closed` | **mismatch** | `closed/archived/none` | `closed/visible_closed/apply` | differs: board_treatment, primary_action<br>missing: evidence_history_and_retries |
| `community_removed_but_destination_live` | **mismatch** | `live/normal/apply` | `closed/visible_closed/apply` | differs: availability, board_treatment<br>missing: cross_source_reconciliation, destination_validation<br>ignored timeline steps: 2 |
| `community_presence_destination_timeout` | **mismatch** | `uncertain/warned/verify` | `live/normal/apply` | differs: availability, board_treatment, primary_action, secondary_action<br>missing: availability_board_treatments, community_pre_display_validation, cross_source_reconciliation, destination_validation, tri_state_availability, verify_action<br>ignored timeline steps: 2 |
| `first_party_timeout_uncertain` | **mismatch** | `uncertain/warned/verify` | `live/normal/apply` | differs: availability, board_treatment, primary_action, secondary_action<br>missing: availability_board_treatments, destination_validation, tri_state_availability, verify_action<br>ignored timeline steps: 1 |
| `dns_failure_uncertain` | **mismatch** | `uncertain/warned/verify` | `live/normal/apply` | differs: availability, board_treatment, primary_action, secondary_action<br>missing: availability_board_treatments, destination_validation, tri_state_availability, verify_action<br>ignored timeline steps: 1 |
| `connection_failure_uncertain` | **mismatch** | `uncertain/warned/verify` | `live/normal/apply` | differs: availability, board_treatment, primary_action, secondary_action<br>missing: availability_board_treatments, destination_validation, tri_state_availability, verify_action<br>ignored timeline steps: 1 |
| `automation_403_keeps_apply` | **mismatch** | `uncertain/normal/apply` | `live/normal/apply` | differs: availability, secondary_action<br>missing: destination_validation, tri_state_availability<br>ignored timeline steps: 1 |
| `anti_bot_keeps_apply` | **mismatch** | `uncertain/normal/apply` | `live/normal/apply` | differs: availability, secondary_action<br>missing: destination_validation, tri_state_availability<br>ignored timeline steps: 1 |
| `javascript_required_keeps_apply` | **mismatch** | `uncertain/normal/apply` | `live/normal/apply` | differs: availability, secondary_action<br>missing: destination_validation, tri_state_availability<br>ignored timeline steps: 1 |
| `login_required_keeps_apply` | **mismatch** | `uncertain/normal/apply` | `live/normal/apply` | differs: availability, secondary_action<br>missing: destination_validation, tri_state_availability<br>ignored timeline steps: 1 |
| `rate_limited_429_outage` | **mismatch** | `uncertain/warned/verify` | `live/normal/apply` | differs: availability, board_treatment, primary_action, secondary_action<br>missing: availability_board_treatments, destination_validation, tri_state_availability, verify_action<br>ignored timeline steps: 1 |
| `service_5xx_outage` | **mismatch** | `uncertain/warned/verify` | `live/normal/apply` | differs: availability, board_treatment, primary_action, secondary_action<br>missing: availability_board_treatments, destination_validation, tri_state_availability, verify_action<br>ignored timeline steps: 1 |
| `maintenance_page_outage` | **mismatch** | `uncertain/warned/verify` | `live/normal/apply` | differs: availability, board_treatment, primary_action, secondary_action<br>missing: availability_board_treatments, destination_validation, tri_state_availability, verify_action<br>ignored timeline steps: 1 |
| `redirect_loop_outage` | **mismatch** | `uncertain/warned/verify` | `live/normal/apply` | differs: availability, board_treatment, primary_action, secondary_action<br>missing: availability_board_treatments, destination_validation, tri_state_availability, verify_action<br>ignored timeline steps: 1 |
| `trustworthy_evidence_conflicts` | **mismatch** | `uncertain/warned/apply` | `closed/visible_closed/apply` | differs: availability, board_treatment, secondary_action<br>missing: availability_board_treatments, cross_source_reconciliation, destination_validation, evidence_history_and_retries, tri_state_availability<br>ignored timeline steps: 3 |
| `original_link_recovered` | **mismatch** | `live/normal/apply` | `live/normal/apply` | differs: investigation_outcome, investigation_stages<br>missing: destination_validation, evidence_history_and_retries, investigation_workflow<br>ignored timeline steps: 1, 2, 3 |
| `replacement_requires_confirmation` | **mismatch** | `uncertain/guarded/confirm_replacement` | `closed/visible_closed/apply` | differs: availability, board_treatment, investigation_outcome, investigation_stages, primary_action, replacement_behavior, replacement_candidate_url, replacement_requires_confirmation, secondary_action<br>missing: availability_board_treatments, cross_source_reconciliation, destination_validation, investigation_workflow, replacement_confirmation, tri_state_availability<br>ignored timeline steps: 1, 4 |
| `investigation_confirms_closure` | **mismatch** | `closed/archived/none` | `closed/visible_closed/apply` | differs: board_treatment, investigation_outcome, investigation_stages, primary_action<br>missing: destination_validation, evidence_history_and_retries, investigation_workflow<br>ignored timeline steps: 1, 2, 4 |
| `investigation_remains_unresolved` | **mismatch** | `uncertain/warned/verify` | `live/normal/apply` | differs: availability, board_treatment, investigation_outcome, investigation_stages, primary_action, secondary_action<br>missing: availability_board_treatments, cross_source_reconciliation, destination_validation, evidence_history_and_retries, investigation_workflow, tri_state_availability, verify_action<br>ignored timeline steps: 1, 2, 4 |
| `point72_cubist_sanitized` | **mismatch** | `uncertain/guarded/confirm_replacement` | `live/normal/apply` | differs: availability, board_treatment, investigation_outcome, investigation_stages, primary_action, replacement_behavior, replacement_candidate_url, replacement_requires_confirmation, secondary_action<br>missing: availability_board_treatments, cross_source_reconciliation, destination_validation, investigation_workflow, replacement_confirmation, tri_state_availability<br>ignored timeline steps: 2, 4 |
| `voloridge_sanitized` | **mismatch** | `uncertain/guarded/confirm_replacement` | `live/normal/apply` | differs: availability, board_treatment, investigation_outcome, investigation_stages, primary_action, replacement_behavior, replacement_candidate_url, replacement_requires_confirmation, secondary_action<br>missing: availability_board_treatments, cross_source_reconciliation, destination_validation, investigation_workflow, replacement_confirmation, tri_state_availability<br>ignored timeline steps: 2, 4 |
| `workday_maintenance_sanitized` | **mismatch** | `uncertain/warned/verify` | `live/normal/apply` | differs: availability, board_treatment, investigation_outcome, investigation_stages, primary_action, secondary_action<br>missing: availability_board_treatments, content_aware_closure, destination_validation, evidence_history_and_retries, investigation_workflow, tri_state_availability, verify_action<br>ignored timeline steps: 1, 3 |

## Reproduce

At production-code revision `a8abdc8`:

```bash
.venv/bin/python tests/availability/current_baseline.py --check
.venv/bin/python -m pytest -m availability_baseline -q
```

This command is offline and read-only with respect to configured sources, the real
database, saved postings, and application state.
