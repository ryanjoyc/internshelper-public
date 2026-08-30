from copy import deepcopy

import pytest

from availability.corpus import (
    CATALOG_PATH,
    CORPUS_PATH,
    COVERAGE_PATH,
    coverage_gaps,
    coverage_index,
    expected_semantics,
    load_corpus,
    validation_errors,
)
from availability.fakes import ScriptExhausted, build_scenario
from availability.generate import generated_documents
from availability.mutants import MUTANTS, caught_case_ids, mutate_case


def test_availability_corpus_manifest_exists():
    assert CORPUS_PATH.is_file(), "the versioned availability corpus is the contract source of truth"


def test_corpus_schema_fixtures_and_coverage_are_valid():
    corpus = load_corpus(validate=False)
    assert validation_errors(corpus) == []
    assert coverage_gaps(corpus) == []


def test_approved_policy_version_is_recorded():
    corpus = load_corpus()
    assert corpus["approval"] == {
        "status": "approved",
        "approved_by": "the user",
        "approved_on": "2026-08-30",
    }


def test_integrity_validator_rejects_schema_fixture_outcome_and_coverage_faults():
    malformed = deepcopy(load_corpus())
    malformed["cases"][1]["id"] = malformed["cases"][0]["id"]
    malformed["cases"][2]["justification"] = ""
    malformed["cases"][3]["evidence_timeline"][0]["fixture"] = "http/missing.html"
    malformed["cases"][4]["expected"]["availability"] = "maybe"
    malformed["cases"][5]["expected"]["ranking_effect"] = "negative"
    malformed["coverage_requirements"]["evidence_signal"].append("never_covered")
    errors = "\n".join(validation_errors(malformed))
    for expected_error in (
        "duplicate case ID",
        "justification must be non-empty",
        "fixture does not exist",
        "expected.availability is invalid",
        "expected.ranking_effect must be none",
        "required coverage gap: evidence_signal=never_covered",
    ):
        assert expected_error in errors


def test_case_ids_are_unique_and_explanations_are_reviewable():
    corpus = load_corpus()
    ids = [case["id"] for case in corpus["cases"]]
    assert len(ids) == len(set(ids))
    assert all(len(case["justification"].split()) >= 6 for case in corpus["cases"])


def test_every_availability_result_has_no_ranking_effect():
    corpus = load_corpus()
    assert {expected_semantics(case)["ranking_effect"] for case in corpus["cases"]} == {"none"}


def test_generated_review_documents_are_current():
    corpus = load_corpus()
    generated = generated_documents(corpus)
    assert set(generated) == {CATALOG_PATH, COVERAGE_PATH}
    for path, expected in generated.items():
        assert path.is_file(), f"missing generated document: {path}"
        assert path.read_text(encoding="utf-8") == expected, (
            f"{path.name} is stale; run .venv/bin/python tests/availability/generate.py"
        )


def test_coverage_matrix_is_derived_from_case_semantics():
    corpus = load_corpus()
    index = coverage_index(corpus)
    assert "community_checked_before_display" in index["source_authority"]["community_list"]
    assert "workday_maintenance_sanitized" in index["known_pattern"]["workday_maintenance"]
    assert "replacement_requires_confirmation" in index["investigation_outcome"]["replacement_found"]


def test_fake_network_preserves_redirect_and_retry_order():
    corpus = load_corpus()
    case = next(case for case in corpus["cases"] if case["id"] == "unknown_redirect_to_live")
    network = build_scenario(case).network
    first = network.request("original_url")
    second = network.request("redirected_url")
    assert (first.status, first.location, second.status) == (
        302,
        "https://jobs.example.test/roles/redirected",
        200,
    )
    assert network.remaining == 0
    scenario = build_scenario(case)
    scenario.network.request("original_url")
    scenario.network.request("redirected_url")
    scenario.assert_consumed({"network"})
    with pytest.raises(ScriptExhausted):
        network.request()


def test_fake_network_preserves_scheduled_retry_attempts():
    corpus = load_corpus()
    case = next(case for case in corpus["cases"] if case["id"] == "repeated_404_closed")
    scenario = build_scenario(case)
    first = scenario.network.request("original_url")
    retry = scenario.network.request("original_url")
    assert (first.attempt, first.stage, retry.attempt, retry.stage) == (
        1,
        "initial_validation",
        2,
        "scheduled_retry",
    )
    scenario.assert_consumed({"network"})


def test_fake_sources_and_investigator_expose_scripted_semantics():
    corpus = load_corpus()
    replacement = next(
        case for case in corpus["cases"] if case["id"] == "replacement_requires_confirmation"
    )
    scenario = build_scenario(replacement)
    scenario.network.request("original_url")
    source_events = []
    while scenario.sources.remaining:
        source_events.append(scenario.sources.enumerate())
    finding = scenario.investigator.finding()
    assert [event.result for event in source_events] == ["absent", "present"]
    assert finding.result == "replacement_found"
    assert finding.candidate_url == "https://jobs.example.test/roles/replacement"
    assert scenario.investigator.progress()[-1] == "await_user_confirmation"
    scenario.assert_consumed()


def test_manifest_mutant_registry_matches_executable_mutants():
    corpus = load_corpus()
    assert set(corpus["mutants"]) == set(MUTANTS)


@pytest.mark.parametrize("mutant_id", sorted(MUTANTS))
def test_corpus_kills_each_policy_mutant(mutant_id):
    corpus = load_corpus()
    caught = caught_case_ids(corpus, mutant_id)
    assert caught, f"policy mutant survived: {mutant_id}"
    assert all(
        mutate_case(case, mutant_id) != expected_semantics(case)
        for case in corpus["cases"]
        if case["id"] in caught
    ), f"listed killer cases did not actually reject {mutant_id}: {caught}"
