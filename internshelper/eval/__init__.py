"""Offline extraction-accuracy eval harness.

Replays saved source responses through the real connectors and scores parsed-vs-golden
(role recall/precision + per-field correctness + storage dedup integrity). Deterministic
and network-free: the bedrock that lets us measure parsing accuracy continuously and catch
regressions, with or without an AI agent.

    from internshelper.eval import score_all, score_fixture, discover_fixtures
"""

from internshelper.eval.harness import (
    discover_fixtures,
    score_all,
    score_fixture,
)
from internshelper.eval.scoring import FixtureResult

__all__ = ["discover_fixtures", "score_all", "score_fixture", "FixtureResult"]
