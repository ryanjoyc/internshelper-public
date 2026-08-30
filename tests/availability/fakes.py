"""Deterministic scripted collaborators for future availability implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


class ScriptExhausted(AssertionError):
    """The implementation made more collaborator calls than the corpus scripted."""


@dataclass(frozen=True)
class EvidenceEvent:
    sequence: int
    at: str
    stage: str
    channel: str
    target: str
    attempt: int
    result: str
    signals: tuple[str, ...]
    note: str
    fixture: str
    status: int | None = None
    location: str | None = None
    candidate_url: str | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "EvidenceEvent":
        return cls(
            sequence=value["sequence"],
            at=value["at"],
            stage=value["stage"],
            channel=value["channel"],
            target=value["target"],
            attempt=value["attempt"],
            result=value["result"],
            signals=tuple(value["signals"]),
            note=value["note"],
            fixture=value["fixture"],
            status=value.get("status"),
            location=value.get("location"),
            candidate_url=value.get("candidate_url"),
        )


class CallLedger:
    """Record cross-collaborator calls so the evidence timeline order is executable."""

    def __init__(self, expected: Iterable[EvidenceEvent]):
        self.expected = tuple(expected)
        self.observed: list[EvidenceEvent] = []

    def record(self, event: EvidenceEvent) -> None:
        self.observed.append(event)

    def assert_consumed(self, channels: set[str] | None = None) -> None:
        selected = channels or {event.channel for event in self.expected}
        expected = [event.sequence for event in self.expected if event.channel in selected]
        observed = [event.sequence for event in self.observed if event.channel in selected]
        assert observed == expected, (
            f"evidence timeline calls were incomplete or out of order: "
            f"expected {expected}, observed {observed}"
        )


class _Script:
    def __init__(
        self,
        events: Iterable[EvidenceEvent],
        *,
        collaborator: str,
        ledger: CallLedger,
    ):
        self._events = list(events)
        self._cursor = 0
        self.collaborator = collaborator
        self.ledger = ledger
        self.calls: list[str] = []

    @property
    def remaining(self) -> int:
        return len(self._events) - self._cursor

    def next(self, target: str | None = None) -> EvidenceEvent:
        if self._cursor >= len(self._events):
            raise ScriptExhausted(f"{self.collaborator} script is exhausted")
        event = self._events[self._cursor]
        if target is not None and event.target != target:
            raise AssertionError(
                f"{self.collaborator} expected target {event.target!r}, got {target!r}"
            )
        self._cursor += 1
        self.calls.append(event.target)
        self.ledger.record(event)
        return event


class FakeNetwork(_Script):
    """Return scripted redirects, responses, and transport failures in timeline order."""

    def __init__(self, events: Iterable[EvidenceEvent], ledger: CallLedger):
        super().__init__(events, collaborator="network", ledger=ledger)

    def request(self, target: str | None = None) -> EvidenceEvent:
        return self.next(target)


class FakeSourceEnumerator(_Script):
    """Return scripted ATS/community presence, absence, removal, or source errors."""

    def __init__(self, events: Iterable[EvidenceEvent], ledger: CallLedger):
        super().__init__(events, collaborator="source enumerator", ledger=ledger)

    def enumerate(self, target: str | None = None) -> EvidenceEvent:
        return self.next(target)


class FakeInvestigator(_Script):
    """Expose scripted findings plus semantic progress stages for UI-agnostic assertions."""

    def __init__(
        self,
        events: Iterable[EvidenceEvent],
        progress_stages: Iterable[str],
        ledger: CallLedger,
    ):
        super().__init__(events, collaborator="investigator", ledger=ledger)
        self._progress_stages = tuple(progress_stages)

    def progress(self) -> tuple[str, ...]:
        return self._progress_stages

    def finding(self, target: str | None = None) -> EvidenceEvent:
        return self.next(target)


@dataclass
class Scenario:
    network: FakeNetwork
    sources: FakeSourceEnumerator
    investigator: FakeInvestigator
    ledger: CallLedger

    def assert_consumed(self, channels: set[str] | None = None) -> None:
        self.ledger.assert_consumed(channels)


def build_scenario(case: dict[str, Any]) -> Scenario:
    events = [EvidenceEvent.from_mapping(event) for event in case["evidence_timeline"]]
    stages = case["expected"]["investigation"]["stages"]
    ledger = CallLedger(events)
    return Scenario(
        network=FakeNetwork(
            (event for event in events if event.channel == "network"), ledger
        ),
        sources=FakeSourceEnumerator(
            (event for event in events if event.channel == "source"), ledger
        ),
        investigator=FakeInvestigator(
            (event for event in events if event.channel == "investigator"), stages, ledger
        ),
        ledger=ledger,
    )
