"""The extraction-accuracy gate: every eval fixture must score a perfect 100%.

Happy-path fixtures (one per connector, from real saved responses) lock in correct
behavior. Adversarial fixtures encode the CORRECT output for a known parser bug, so they
fail RED against the pre-fix parser — each is `xfail(strict=True)` until its bug is fixed.
`strict` means the moment a fix lands and the fixture passes, pytest FAILS until its entry
is removed from `KNOWN_RED`, forcing every fix to retire its own marker. End state: an empty
`KNOWN_RED` and an all-green suite.
"""

from __future__ import annotations

import pytest

from internshelper.eval import harness

FIXTURES = harness.discover_fixtures()

# Adversarial fixtures still failing (bug not yet fixed). Remove an entry the moment its
# fix lands — its xfail(strict) will otherwise turn the now-passing test into a failure.
# All 11 bugs are fixed, so this is empty: every fixture must now score a perfect 100%.
KNOWN_RED: dict[str, str] = {}


def _param(fx):
    marks = (pytest.mark.xfail(strict=True, reason=KNOWN_RED[fx.id])
             if fx.id in KNOWN_RED else ())
    return pytest.param(fx, id=fx.id, marks=marks)


def test_fixtures_discovered():
    assert FIXTURES, "no eval fixtures discovered under tests/fixtures/eval/"


@pytest.mark.parametrize("fx", [_param(f) for f in FIXTURES])
def test_golden(fx):
    """Parse a fixture's saved payload and assert a perfect score vs its golden."""
    result = harness.score_fixture(fx)
    assert result.is_perfect(), "\n" + result.diff()


@pytest.mark.parametrize(
    "fx", [pytest.param(f, id=f.id) for f in FIXTURES if f.id not in KNOWN_RED]
)
def test_storage_roundtrip_idempotent_and_no_merge(fx):
    """The storage round-trip on a (currently-green) fixture is idempotent and never
    silently merges two distinct postings onto one posting_id."""
    postings = harness.run_connector(fx)
    s = harness.storage_roundtrip(postings)
    assert s["idempotent"], f"re-upsert grew the table: {s}"
    assert s["no_merge"], f"distinct postings merged on store: {s}"
