"""Runtime collaborators for availability checks.

The HTTP client is deliberately separate from interpretation and policy so tests can
replace it with deterministic observations and the Board never depends on httpx.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from internshelper.availability_checks import (
    CheckStage,
    NetworkFailure,
    NetworkObservation,
)
from internshelper.connectors.base import HEADERS, TIMEOUT


class _UnsafeDestination(ValueError):
    pass


class _ResolutionFailure(OSError):
    pass


def _resolve_host(host: str) -> tuple[str, ...]:
    try:
        answers = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise _ResolutionFailure(str(exc)) from exc
    return tuple(dict.fromkeys(answer[4][0] for answer in answers))


@dataclass
class HttpDestinationChecker:
    client: httpx.Client | None = None
    resolver: Callable[[str], Iterable[str]] = _resolve_host
    max_redirects: int = 10

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = httpx.Client(
                timeout=TIMEOUT,
                headers=HEADERS,
                follow_redirects=False,
            )

    def check(
        self,
        url: str,
        *,
        sequence: int,
        attempt: int,
        stage: CheckStage,
        observed_at: str,
        employer_hosted: bool,
        target: str = "original_url",
    ) -> tuple[NetworkObservation, ...]:
        """GET one destination and retain redirect steps or a normalized failure."""

        assert self.client is not None
        original_host = (urlparse(url).hostname or "").lower()
        observations: list[NetworkObservation] = []
        current_url = url
        seen: set[str] = set()
        redirects = 0
        while True:
            if current_url in seen:
                observations.append(
                    self._failure(
                        NetworkFailure.REDIRECT_LOOP,
                        sequence + len(observations),
                        attempt,
                        stage,
                        observed_at,
                        str(current_url),
                        False,
                    )
                )
                return tuple(observations)
            seen.add(current_url)
            try:
                current_host = self._validated_host(current_url)
            except _ResolutionFailure:
                observations.append(
                    self._failure(
                        NetworkFailure.DNS,
                        sequence + len(observations),
                        attempt,
                        stage,
                        observed_at,
                        str(current_url),
                        False,
                    )
                )
                return tuple(observations)
            except _UnsafeDestination:
                observations.append(
                    self._failure(
                        NetworkFailure.UNSAFE_DESTINATION,
                        sequence + len(observations),
                        attempt,
                        stage,
                        observed_at,
                        str(current_url),
                        False,
                    )
                )
                return tuple(observations)
            trusted_host = employer_hosted and current_host == original_host
            try:
                response = self.client.get(current_url, follow_redirects=False)
            except httpx.TimeoutException:
                failure = NetworkFailure.TIMEOUT
            except httpx.ConnectError as exc:
                detail = str(exc).lower()
                failure = (
                    NetworkFailure.DNS
                    if any(
                        phrase in detail
                        for phrase in ("name resolution", "getaddrinfo", "nodename")
                    )
                    else NetworkFailure.CONNECTION
                )
            except httpx.RequestError:
                failure = NetworkFailure.CONNECTION
            else:
                failure = None
            if failure is not None:
                observations.append(
                    self._failure(
                        failure,
                        sequence + len(observations),
                        attempt,
                        stage,
                        observed_at,
                        str(current_url),
                        trusted_host,
                    )
                )
                return tuple(observations)

            raw_location = response.headers.get("location")
            location = (
                str(response.request.url.join(raw_location))
                if raw_location
                else None
            )
            observations.append(
                NetworkObservation(
                    sequence=sequence + len(observations),
                    observed_at=observed_at,
                    stage=stage,
                    target=target if len(observations) == 0 else str(response.request.url),
                    attempt=attempt,
                    status=response.status_code,
                    body=response.text,
                    location=location,
                    employer_hosted=trusted_host,
                    checker_only=response.status_code == 403,
                )
            )
            if not 300 <= response.status_code <= 399 or location is None:
                return tuple(observations)
            redirects += 1
            if redirects > self.max_redirects:
                observations.append(
                    self._failure(
                        NetworkFailure.REDIRECT_LOOP,
                        sequence + len(observations),
                        attempt,
                        stage,
                        observed_at,
                        location,
                        False,
                    )
                )
                return tuple(observations)
            current_url = location

    def _validated_host(self, url: str) -> str:
        parsed = urlparse(url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise _UnsafeDestination("destination port is invalid") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise _UnsafeDestination("destination must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None or port == 0:
            raise _UnsafeDestination("destination contains unsafe authority fields")
        host = parsed.hostname.lower()
        if host == "localhost" or host.endswith(".localhost") or "%" in host:
            raise _UnsafeDestination("local destinations are not allowed")
        try:
            addresses = (ipaddress.ip_address(host),)
        except ValueError:
            try:
                resolved = tuple(self.resolver(host))
            except _ResolutionFailure:
                raise
            except OSError as exc:
                raise _ResolutionFailure(str(exc)) from exc
            if not resolved:
                raise _ResolutionFailure("destination host did not resolve")
            try:
                addresses = tuple(
                    ipaddress.ip_address(value.split("%", 1)[0]) for value in resolved
                )
            except ValueError as exc:
                raise _ResolutionFailure("resolver returned an invalid address") from exc
        if any(not address.is_global for address in addresses):
            raise _UnsafeDestination("destination resolved to a non-public address")
        return host

    @staticmethod
    def _failure(
        failure: NetworkFailure,
        sequence: int,
        attempt: int,
        stage: CheckStage,
        observed_at: str,
        target: str,
        employer_hosted: bool,
    ) -> NetworkObservation:
        return NetworkObservation(
            sequence=sequence,
            observed_at=observed_at,
            stage=stage,
            target=target,
            attempt=attempt,
            failure=failure,
            employer_hosted=employer_hosted,
        )

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
