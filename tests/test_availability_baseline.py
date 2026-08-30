"""Regression checks for the pre-implementation availability baseline."""

from __future__ import annotations

import pytest

from availability.corpus import load_corpus
from availability.current_baseline import (
    BASELINE_PATH,
    REPORT_PATH,
    load_baseline,
    render_report,
    run_baseline,
)


@pytest.mark.availability_baseline
def test_current_adapter_reports_supported_mismatches_without_touching_real_data(tmp_path):
    corpus = load_corpus()

    baseline = run_baseline(corpus, work_dir=tmp_path)

    assert len(baseline["results"]) == len(corpus["cases"]) == 34
    assert {result["status"] for result in baseline["results"]} <= {
        "pass",
        "mismatch",
        "unsupported",
    }
    assert len(list(tmp_path.glob("*.db"))) == 34

    by_id = {result["case_id"]: result for result in baseline["results"]}
    assert by_id["ats_live_initial"]["status"] == "unsupported"
    assert "destination_validation" in by_id["ats_live_initial"]["unsupported_capabilities"]

    # Current close detection closes after one complete source absence, while the approved
    # policy requires an uncertain retry state.
    absence = by_id["ats_absence_once_uncertain"]
    assert absence["status"] == "mismatch"
    assert absence["observed"]["availability"] == "closed"
    assert "availability" in absence["mismatched_fields"]

    # A destination-only 404 is invisible to today's collector, so the row remains active.
    missing = by_id["repeated_404_closed"]
    assert missing["status"] == "mismatch"
    assert missing["observed"]["availability"] == "live"
    assert missing["ignored_evidence"] == [1, 2]


def test_checked_in_baseline_and_report_are_complete_and_in_sync():
    corpus = load_corpus()
    baseline = load_baseline()

    assert BASELINE_PATH.is_file()
    assert REPORT_PATH.is_file()
    assert baseline["corpus_policy_version"] == corpus["policy_version"]
    assert baseline["corpus_approval"] == corpus["approval"]
    assert [result["case_id"] for result in baseline["results"]] == [
        case["id"] for case in corpus["cases"]
    ]
    assert sum(baseline["summary"].values()) == len(corpus["cases"])
    assert baseline["summary"] == {"mismatch": 29, "pass": 0, "unsupported": 5}
    assert REPORT_PATH.read_text(encoding="utf-8") == render_report(baseline)
