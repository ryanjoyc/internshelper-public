"""One stable seam between the corpus and the production availability system."""

from __future__ import annotations

import importlib
import os
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from .fakes import Scenario


ADAPTER_ENV = "INTERNSHELPER_AVAILABILITY_CONTRACT_ADAPTER"
DEFAULT_ADAPTER = "availability.production_adapter:create_adapter"


class ContractNotImplemented(RuntimeError):
    """An adapter method has not been implemented."""


class AvailabilityContractAdapter(Protocol):
    """Production implementations translate their own objects at this boundary."""

    def decide(self, case: dict[str, Any]) -> Mapping[str, Any]: ...

    def interpret(self, case: dict[str, Any], scenario: Scenario) -> Mapping[str, Any]: ...

    def collect_to_board(
        self, case: dict[str, Any], scenario: Scenario, db_path: Path
    ) -> Mapping[str, Any]: ...

    def investigate(self, case: dict[str, Any], scenario: Scenario) -> Mapping[str, Any]: ...


def load_contract_adapter() -> AvailabilityContractAdapter:
    """Load the test translation seam without imposing a production module layout."""

    spec = os.environ.get(ADAPTER_ENV, DEFAULT_ADAPTER)
    module_name, separator, factory_name = spec.partition(":")
    if not separator or not module_name or not factory_name:
        raise ValueError(f"{ADAPTER_ENV} must use module:factory syntax")
    factory = getattr(importlib.import_module(module_name), factory_name)
    adapter = factory()
    required = {"decide", "interpret", "collect_to_board", "investigate"}
    missing = sorted(name for name in required if not callable(getattr(adapter, name, None)))
    if missing:
        raise TypeError(f"availability contract adapter is missing methods: {', '.join(missing)}")
    return adapter


def result_mapping(result: Any) -> dict[str, Any]:
    if is_dataclass(result):
        return asdict(result)
    if isinstance(result, Mapping):
        return dict(result)
    raise TypeError("availability contract adapter results must be mappings or dataclasses")
