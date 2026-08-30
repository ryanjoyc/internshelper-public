"""Load and validate the test-only availability contract corpus."""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


HERE = Path(__file__).parent
CORPUS_PATH = HERE / "corpus.yaml"
FIXTURES_PATH = HERE / "fixtures"
CATALOG_PATH = HERE.parents[1] / "docs" / "availability-verification-catalog.md"
COVERAGE_PATH = HERE.parents[1] / "docs" / "availability-verification-coverage.md"

CASE_ID = re.compile(r"^[a-z][a-z0-9_]+$")
SOURCE_AUTHORITIES = {"first_party_ats", "community_list", "unknown"}
PROVENANCE_KINDS = {"synthetic", "sanitized_real"}
USER_STATES = {"ordinary", "interested", "applied"}
AVAILABILITIES = {"live", "uncertain", "closed"}
BOARD_TREATMENTS = {"normal", "warned", "guarded", "archived", "visible_closed"}
ACTIONS = {"apply", "verify", "confirm_replacement", "open_original", "none"}
CHECK_STAGES = {"initial_validation", "scheduled_retry", "user_investigation"}
CHANNELS = {"network", "source", "investigator"}
LAYERS = {"policy", "network", "board", "investigation"}
INVESTIGATION_OUTCOMES = {
    "not_run", "original_recovered", "replacement_found", "closure_confirmed", "unresolved"
}
REPLACEMENT_BEHAVIORS = {"none", "offer_for_confirmation"}
STORED_URL_STATES = {"original", "candidate"}

REQUIRED_DIMENSIONS = {
    "source_authority",
    "evidence_signal",
    "user_state",
    "check_stage",
    "investigation_outcome",
    "availability",
    "board_treatment",
    "layer",
    "known_pattern",
}


class CorpusValidationError(ValueError):
    """Raised when the checked-in corpus does not satisfy its schema or coverage contract."""


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def load_corpus(path: Path = CORPUS_PATH, *, validate: bool = True) -> dict[str, Any]:
    """Read the YAML manifest and optionally reject every discovered contract error."""

    corpus = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(corpus, dict):
        raise CorpusValidationError("corpus root must be a mapping")
    if validate:
        errors = validation_errors(corpus, fixtures_path=path.parent / "fixtures")
        if errors:
            joined = "\n- ".join(errors)
            raise CorpusValidationError(f"availability corpus is invalid:\n- {joined}")
    return corpus


def validation_errors(
    corpus: dict[str, Any], *, fixtures_path: Path = FIXTURES_PATH
) -> list[str]:
    """Return all schema, fixture, and required-coverage errors in one pass."""

    errors: list[str] = []
    required_top = {
        "schema_version",
        "policy_version",
        "approval",
        "coverage_requirements",
        "policy_rules",
        "mutants",
        "maintenance_rules",
        "live_canaries",
        "cases",
    }
    missing_top = sorted(required_top - set(corpus))
    if missing_top:
        errors.append(f"missing top-level keys: {', '.join(missing_top)}")

    if corpus.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if not _is_nonempty_string(corpus.get("policy_version")):
        errors.append("policy_version must be a non-empty string")

    approval = _mapping(corpus.get("approval"))
    if approval.get("status") != "approved":
        errors.append("approval.status must be approved")
    for field in ("approved_by", "approved_on"):
        if not _is_nonempty_string(approval.get(field)):
            errors.append(f"approval.{field} must be non-empty")

    coverage_requirements = _mapping(corpus.get("coverage_requirements"))
    missing_dimensions = sorted(REQUIRED_DIMENSIONS - set(coverage_requirements))
    if missing_dimensions:
        errors.append(f"coverage_requirements missing: {', '.join(missing_dimensions)}")
    for dimension, values in coverage_requirements.items():
        if not isinstance(values, list) or not values or any(not _is_nonempty_string(v) for v in values):
            errors.append(f"coverage_requirements.{dimension} must be a non-empty string list")
        elif len(values) != len(set(values)):
            errors.append(f"coverage_requirements.{dimension} contains duplicates")

    policy_rules = _mapping(corpus.get("policy_rules"))
    if not policy_rules or any(
        not CASE_ID.fullmatch(str(rule_id)) or not _is_nonempty_string(statement)
        for rule_id, statement in policy_rules.items()
    ):
        errors.append("policy_rules must map stable IDs to non-empty statements")

    mutants = _mapping(corpus.get("mutants"))
    if not mutants or any(
        not CASE_ID.fullmatch(str(mutant_id)) or not _is_nonempty_string(description)
        for mutant_id, description in mutants.items()
    ):
        errors.append("mutants must map stable IDs to non-empty descriptions")

    maintenance_rules = corpus.get("maintenance_rules")
    if (
        not isinstance(maintenance_rules, list)
        or not maintenance_rules
        or any(not _is_nonempty_string(rule) for rule in maintenance_rules)
    ):
        errors.append("maintenance_rules must be a non-empty string list")

    canary_ids: set[str] = set()
    for index, canary in enumerate(_list(corpus.get("live_canaries"))):
        prefix = f"live_canaries[{index}]"
        if not isinstance(canary, dict):
            errors.append(f"{prefix} must be a mapping")
            continue
        canary_id = canary.get("id")
        if not _is_nonempty_string(canary_id) or not CASE_ID.fullmatch(canary_id):
            errors.append(f"{prefix}.id must be a stable snake_case ID")
        elif canary_id in canary_ids:
            errors.append(f"duplicate live canary ID: {canary_id}")
        else:
            canary_ids.add(canary_id)
        if canary.get("method") not in {"GET", "POST"}:
            errors.append(f"{prefix}.method must be GET or POST")
        if not _is_nonempty_string(canary.get("url")) or not canary["url"].startswith("https://"):
            errors.append(f"{prefix}.url must be a non-empty HTTPS URL")
        if canary.get("authority") not in SOURCE_AUTHORITIES:
            errors.append(f"{prefix}.authority is invalid")
        if canary.get("mutates") is not False:
            errors.append(f"{prefix}.mutates must be false")

    cases = _list(corpus.get("cases"))
    if not cases:
        errors.append("cases must be a non-empty list")

    seen_ids: set[str] = set()
    for index, case in enumerate(cases):
        prefix = f"cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{prefix} must be a mapping")
            continue

        case_id = case.get("id")
        label = case_id if _is_nonempty_string(case_id) else prefix
        if not _is_nonempty_string(case_id) or not CASE_ID.fullmatch(case_id):
            errors.append(f"{prefix}.id must be a stable snake_case ID")
        elif case_id in seen_ids:
            errors.append(f"duplicate case ID: {case_id}")
        else:
            seen_ids.add(case_id)

        for field in ("category", "description", "justification"):
            if not _is_nonempty_string(case.get(field)):
                errors.append(f"{label}.{field} must be non-empty")
        if len(str(case.get("justification", "")).strip()) < 30:
            errors.append(f"{label}.justification must explain the expected outcome")

        provenance = _mapping(case.get("provenance"))
        if provenance.get("kind") not in PROVENANCE_KINDS:
            errors.append(f"{label}.provenance.kind is invalid")
        if not _is_nonempty_string(provenance.get("note")):
            errors.append(f"{label}.provenance.note must be non-empty")
        if provenance.get("kind") == "sanitized_real":
            if provenance.get("sanitized") is not True:
                errors.append(f"{label}.provenance.sanitized must be true for real-derived cases")
            if not _is_nonempty_string(provenance.get("pattern")):
                errors.append(f"{label}.provenance.pattern is required for real-derived cases")

        source = _mapping(case.get("source"))
        if source.get("authority") not in SOURCE_AUTHORITIES:
            errors.append(f"{label}.source.authority is invalid")
        for field in ("kind", "label"):
            if not _is_nonempty_string(source.get(field)):
                errors.append(f"{label}.source.{field} must be non-empty")

        timeline = _list(case.get("evidence_timeline"))
        if not timeline:
            errors.append(f"{label}.evidence_timeline must be non-empty")
        sequences: list[Any] = []
        for event_index, event in enumerate(timeline):
            event_prefix = f"{label}.evidence_timeline[{event_index}]"
            if not isinstance(event, dict):
                errors.append(f"{event_prefix} must be a mapping")
                continue
            sequences.append(event.get("sequence"))
            if event.get("stage") not in CHECK_STAGES:
                errors.append(f"{event_prefix}.stage is invalid")
            if event.get("channel") not in CHANNELS:
                errors.append(f"{event_prefix}.channel is invalid")
            for field in ("at", "target", "result", "note", "fixture"):
                if not _is_nonempty_string(event.get(field)):
                    errors.append(f"{event_prefix}.{field} must be non-empty")
            signals = event.get("signals")
            if not isinstance(signals, list) or not signals or any(
                not _is_nonempty_string(signal) for signal in signals
            ):
                errors.append(f"{event_prefix}.signals must be a non-empty string list")
            if "status" in event and (
                not isinstance(event["status"], int) or not 100 <= event["status"] <= 599
            ):
                errors.append(f"{event_prefix}.status must be an HTTP status integer")
            attempt = event.get("attempt")
            if not isinstance(attempt, int) or attempt < 1:
                errors.append(f"{event_prefix}.attempt must be a positive integer")

            fixture = event.get("fixture")
            if _is_nonempty_string(fixture):
                relative = Path(fixture)
                if relative.is_absolute() or ".." in relative.parts:
                    errors.append(f"{event_prefix}.fixture must stay inside the fixture directory")
                elif not (fixtures_path / relative).is_file():
                    errors.append(f"{event_prefix}.fixture does not exist: {fixture}")

        if sequences and sequences != list(range(1, len(sequences) + 1)):
            errors.append(f"{label}.evidence_timeline sequence must be contiguous from 1")

        if case.get("user_state") not in USER_STATES:
            errors.append(f"{label}.user_state is invalid")

        expected = _mapping(case.get("expected"))
        if expected.get("availability") not in AVAILABILITIES:
            errors.append(f"{label}.expected.availability is invalid")
        if expected.get("board_treatment") not in BOARD_TREATMENTS:
            errors.append(f"{label}.expected.board_treatment is invalid")
        for field in ("primary_action", "secondary_action"):
            if expected.get(field) not in ACTIONS:
                errors.append(f"{label}.expected.{field} is invalid")
        if expected.get("ranking_effect") != "none":
            errors.append(f"{label}.expected.ranking_effect must be none")

        investigation = _mapping(expected.get("investigation"))
        outcome = investigation.get("outcome")
        if outcome not in INVESTIGATION_OUTCOMES:
            errors.append(f"{label}.expected.investigation.outcome is invalid")
        stages = investigation.get("stages")
        if not isinstance(stages, list) or any(not _is_nonempty_string(stage) for stage in stages):
            errors.append(f"{label}.expected.investigation.stages must be a string list")
        if outcome != "not_run" and not stages:
            errors.append(f"{label}.expected.investigation.stages are required for an investigation")

        replacement = _mapping(expected.get("replacement"))
        if replacement.get("behavior") not in REPLACEMENT_BEHAVIORS:
            errors.append(f"{label}.expected.replacement.behavior is invalid")
        if not isinstance(replacement.get("requires_confirmation"), bool):
            errors.append(f"{label}.expected.replacement.requires_confirmation must be boolean")
        if replacement.get("stored_url") not in STORED_URL_STATES:
            errors.append(f"{label}.expected.replacement.stored_url is invalid")
        candidate_url = replacement.get("candidate_url")
        if candidate_url is not None and not _is_nonempty_string(candidate_url):
            errors.append(f"{label}.expected.replacement.candidate_url must be null or non-empty")
        if replacement.get("behavior") == "offer_for_confirmation":
            if replacement.get("requires_confirmation") is not True:
                errors.append(f"{label} replacement offers must require confirmation")
            if replacement.get("stored_url") != "original" or not candidate_url:
                errors.append(f"{label} replacement offers must preserve the original URL and name a candidate")

        layers = case.get("layers")
        if not isinstance(layers, list) or not layers or any(layer not in LAYERS for layer in layers):
            errors.append(f"{label}.layers must use known contract layers")

        case_rules = case.get("policy_rules")
        if not isinstance(case_rules, list) or not case_rules:
            errors.append(f"{label}.policy_rules must be non-empty")
        else:
            unknown_rules = sorted(set(case_rules) - set(policy_rules))
            if unknown_rules:
                errors.append(f"{label}.policy_rules unknown IDs: {', '.join(unknown_rules)}")

    if cases and coverage_requirements:
        index = coverage_index(corpus)
        for dimension, required_values in coverage_requirements.items():
            allowed_values = set(required_values) if isinstance(required_values, list) else set()
            unknown_values = sorted(set(index.get(dimension, {})) - allowed_values)
            if unknown_values:
                errors.append(
                    f"unknown {dimension} values: {', '.join(unknown_values)}"
                )
            for value in required_values if isinstance(required_values, list) else []:
                if not index.get(dimension, {}).get(value):
                    errors.append(f"required coverage gap: {dimension}={value}")

    rule_index = policy_rule_index(corpus)
    for rule_id in policy_rules:
        if not rule_index.get(rule_id):
            errors.append(f"policy rule has no cases: {rule_id}")

    return errors


def coverage_index(corpus: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    """Derive the coverage matrix from semantic case fields, never handwritten tags."""

    index: dict[str, dict[str, list[str]]] = {
        dimension: {} for dimension in REQUIRED_DIMENSIONS
    }

    def add(dimension: str, value: str | None, case_id: str) -> None:
        if value and value != "not_run":
            case_ids = index[dimension].setdefault(value, [])
            if case_id not in case_ids:
                case_ids.append(case_id)

    for case in _list(corpus.get("cases")):
        if not isinstance(case, dict) or not _is_nonempty_string(case.get("id")):
            continue
        case_id = case["id"]
        source = _mapping(case.get("source"))
        expected = _mapping(case.get("expected"))
        investigation = _mapping(expected.get("investigation"))
        provenance = _mapping(case.get("provenance"))
        add("source_authority", source.get("authority"), case_id)
        add("user_state", case.get("user_state"), case_id)
        add("investigation_outcome", investigation.get("outcome"), case_id)
        add("availability", expected.get("availability"), case_id)
        add("board_treatment", expected.get("board_treatment"), case_id)
        add("known_pattern", provenance.get("pattern"), case_id)
        for layer in _list(case.get("layers")):
            add("layer", layer, case_id)
        for event in _list(case.get("evidence_timeline")):
            if not isinstance(event, dict):
                continue
            add("check_stage", event.get("stage"), case_id)
            for signal in _list(event.get("signals")):
                add("evidence_signal", signal, case_id)
    return index


def coverage_gaps(corpus: dict[str, Any]) -> list[str]:
    """Return required dimension/value pairs with no derived case coverage."""

    index = coverage_index(corpus)
    gaps: list[str] = []
    for dimension, values in _mapping(corpus.get("coverage_requirements")).items():
        for value in _list(values):
            if not index.get(dimension, {}).get(value):
                gaps.append(f"{dimension}={value}")
    return gaps


def policy_rule_index(corpus: dict[str, Any]) -> dict[str, list[str]]:
    index = {rule_id: [] for rule_id in _mapping(corpus.get("policy_rules"))}
    for case in _list(corpus.get("cases")):
        if not isinstance(case, dict) or not _is_nonempty_string(case.get("id")):
            continue
        for rule_id in _list(case.get("policy_rules")):
            index.setdefault(rule_id, []).append(case["id"])
    return index


def expected_semantics(case: dict[str, Any]) -> dict[str, Any]:
    """Flatten UI-relevant expectations into the adapter's stable comparison shape."""

    expected = _mapping(case["expected"])
    investigation = _mapping(expected["investigation"])
    replacement = _mapping(expected["replacement"])
    return {
        "availability": expected["availability"],
        "board_treatment": expected["board_treatment"],
        "primary_action": expected["primary_action"],
        "secondary_action": expected["secondary_action"],
        "ranking_effect": expected["ranking_effect"],
        "investigation_outcome": investigation["outcome"],
        "investigation_stages": list(investigation["stages"]),
        "replacement_behavior": replacement["behavior"],
        "replacement_requires_confirmation": replacement["requires_confirmation"],
        "replacement_candidate_url": replacement.get("candidate_url"),
        "stored_url": replacement["stored_url"],
    }


def clone_expected_semantics(case: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(expected_semantics(case))
