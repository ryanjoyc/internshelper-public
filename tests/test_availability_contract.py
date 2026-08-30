"""Future availability behavior contracts, isolated behind one test adapter."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from availability.adapter import (
    ContractNotImplemented,
    load_contract_adapter,
    result_mapping,
)
from availability.corpus import expected_semantics, load_corpus
from availability.fakes import build_scenario


pytestmark = pytest.mark.availability_contract
CORPUS = load_corpus()


def _cases(layer: str):
    return [case for case in CORPUS["cases"] if layer in case["layers"]]


def _run_or_xfail(operation: Callable[[], Any]) -> dict[str, Any]:
    try:
        return result_mapping(operation())
    except ContractNotImplemented as exc:
        pytest.xfail(str(exc))


def _assert_expected(actual: dict[str, Any], case: dict[str, Any], keys: set[str] | None = None):
    expected = expected_semantics(case)
    selected = keys or set(expected)
    missing = sorted(selected - set(actual))
    assert not missing, f"adapter omitted semantic fields: {', '.join(missing)}"
    assert {key: actual[key] for key in selected} == {key: expected[key] for key in selected}


@pytest.mark.parametrize("case", _cases("policy"), ids=lambda case: case["id"])
def test_evidence_to_policy_decision(case):
    adapter = load_contract_adapter()
    actual = _run_or_xfail(lambda: adapter.decide(case))
    _assert_expected(actual, case)


@pytest.mark.parametrize("case", _cases("network"), ids=lambda case: case["id"])
def test_scripted_network_and_source_interpretation(case):
    adapter = load_contract_adapter()
    scenario = build_scenario(case)
    actual = _run_or_xfail(lambda: adapter.interpret(case, scenario))
    scenario.assert_consumed({"network", "source"})
    _assert_expected(actual, case)


@pytest.mark.parametrize("case", _cases("board"), ids=lambda case: case["id"])
def test_temporary_database_collection_to_board(case, tmp_path, monkeypatch):
    adapter = load_contract_adapter()
    db_path = tmp_path / "availability-contract.db"
    monkeypatch.setenv("INTERNSHELPER_DB", str(db_path))
    scenario = build_scenario(case)
    actual = _run_or_xfail(lambda: adapter.collect_to_board(case, scenario, db_path))
    assert db_path.is_file(), "board-layer adapters must exercise the supplied temporary database"
    scenario.assert_consumed()
    _assert_expected(
        actual,
        case,
        {
            "availability",
            "board_treatment",
            "primary_action",
            "secondary_action",
            "ranking_effect",
            "stored_url",
        },
    )


@pytest.mark.parametrize("case", _cases("investigation"), ids=lambda case: case["id"])
def test_verify_progress_and_recovery(case):
    adapter = load_contract_adapter()
    scenario = build_scenario(case)
    actual = _run_or_xfail(lambda: adapter.investigate(case, scenario))
    scenario.assert_consumed()
    _assert_expected(
        actual,
        case,
        {
            "availability",
            "board_treatment",
            "primary_action",
            "secondary_action",
            "investigation_outcome",
            "investigation_stages",
            "replacement_behavior",
            "replacement_requires_confirmation",
            "replacement_candidate_url",
            "stored_url",
        },
    )
