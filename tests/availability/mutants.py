"""Deliberately wrong test-only policies used to measure corpus fault detection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .corpus import clone_expected_semantics, expected_semantics


Mutation = Callable[[dict[str, Any], dict[str, Any]], bool]


@dataclass(frozen=True)
class PolicyMutant:
    id: str
    mutate: Mutation


def _signals(case: dict[str, Any]) -> set[str]:
    return {
        signal
        for event in case["evidence_timeline"]
        for signal in event["signals"]
    }


def _archive_every_uncertain(case, decision):
    if decision["availability"] != "uncertain":
        return False
    decision["board_treatment"] = "archived"
    return True


def _trust_community_presence(case, decision):
    if "community_presence" not in _signals(case) or decision["availability"] == "live":
        return False
    decision.update(availability="live", board_treatment="normal", primary_action="apply")
    return True


def _never_close(case, decision):
    if decision["availability"] != "closed":
        return False
    decision.update(availability="live", board_treatment="normal", primary_action="apply")
    return True


def _close_after_one_ambiguous_failure(case, decision):
    ambiguous = {
        "generic_error_text",
        "timeout",
        "dns_failure",
        "connection_failure",
        "http_429",
        "http_5xx",
        "maintenance",
        "redirect_loop",
    }
    if decision["availability"] != "uncertain" or not (_signals(case) & ambiguous):
        return False
    decision.update(availability="closed", board_treatment="archived", primary_action="none")
    return True


def _archive_protected_jobs(case, decision):
    if case["user_state"] not in {"interested", "applied"} or decision["availability"] != "closed":
        return False
    decision["board_treatment"] = "archived"
    return True


def _closure_negative_ranking(case, decision):
    if decision["availability"] != "closed":
        return False
    decision["ranking_effect"] = "negative"
    return True


def _guard_apply_after_automation_403(case, decision):
    if "http_403" not in _signals(case):
        return False
    decision.update(board_treatment="guarded", primary_action="verify")
    return True


def _apply_primary_during_outage(case, decision):
    if "user_outage_verify_primary" not in case["policy_rules"]:
        return False
    decision["primary_action"] = "apply"
    return True


def _replace_without_confirmation(case, decision):
    if decision["replacement_behavior"] != "offer_for_confirmation":
        return False
    decision.update(
        replacement_behavior="automatic",
        replacement_requires_confirmation=False,
        stored_url="candidate",
    )
    return True


def _conflict_definitively_closed(case, decision):
    if "conflicting_evidence" not in _signals(case):
        return False
    decision.update(availability="closed", board_treatment="archived", primary_action="none")
    return True


MUTANTS = {
    mutant.id: mutant
    for mutant in (
        PolicyMutant("archive_every_uncertain", _archive_every_uncertain),
        PolicyMutant("trust_community_presence", _trust_community_presence),
        PolicyMutant("never_close_anything", _never_close),
        PolicyMutant("close_after_one_ambiguous_failure", _close_after_one_ambiguous_failure),
        PolicyMutant("archive_protected_jobs", _archive_protected_jobs),
        PolicyMutant("closure_negative_ranking", _closure_negative_ranking),
        PolicyMutant("guard_apply_after_automation_403", _guard_apply_after_automation_403),
        PolicyMutant("apply_primary_during_outage", _apply_primary_during_outage),
        PolicyMutant("replace_without_confirmation", _replace_without_confirmation),
        PolicyMutant("conflict_definitively_closed", _conflict_definitively_closed),
    )
}


def mutate_case(case: dict[str, Any], mutant_id: str) -> dict[str, Any] | None:
    decision = clone_expected_semantics(case)
    if not MUTANTS[mutant_id].mutate(case, decision):
        return None
    return decision


def caught_case_ids(corpus: dict[str, Any], mutant_id: str) -> list[str]:
    caught: list[str] = []
    for case in corpus["cases"]:
        mutated = mutate_case(case, mutant_id)
        if mutated is not None and mutated != expected_semantics(case):
            caught.append(case["id"])
    return caught
