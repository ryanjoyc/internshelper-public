"""Offline tests for the real HTTP availability-check boundary."""

from __future__ import annotations

import httpx

from internshelper.availability_checks import CheckStage, NetworkFailure
from internshelper.availability_runtime import HttpDestinationChecker


def _public_resolver(_host: str):
    return ("93.184.216.34",)


def test_http_checker_preserves_redirect_history_and_final_body():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/original":
            return httpx.Response(302, headers={"Location": "/role"}, request=request)
        return httpx.Response(
            200,
            text="<h1>Software Engineering Intern</h1><button>Apply now</button>",
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    checker = HttpDestinationChecker(client=client, resolver=_public_resolver)

    observations = checker.check(
        "https://jobs.example.test/original",
        sequence=4,
        attempt=2,
        stage=CheckStage.SCHEDULED_RETRY,
        observed_at="2026-08-30T12:00:00+00:00",
        employer_hosted=True,
    )

    assert [item.sequence for item in observations] == [4, 5]
    assert [item.status for item in observations] == [302, 200]
    assert observations[0].location == "https://jobs.example.test/role"
    assert "Apply now" in observations[1].body
    assert observations[1].employer_hosted is True


def test_http_checker_does_not_inherit_employer_trust_across_hosts():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "jobs.example.test":
            return httpx.Response(
                302,
                headers={"Location": "https://unrelated.example/role"},
                request=request,
            )
        return httpx.Response(
            200,
            text="This position is no longer accepting applications",
            request=request,
        )

    checker = HttpDestinationChecker(
        client=httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=True
        ),
        resolver=_public_resolver,
    )

    observations = checker.check(
        "https://jobs.example.test/original",
        sequence=1,
        attempt=1,
        stage=CheckStage.INITIAL_VALIDATION,
        observed_at="2026-08-30T12:00:00+00:00",
        employer_hosted=True,
    )

    assert [item.status for item in observations] == [302, 200]
    assert observations[-1].employer_hosted is False


def test_http_checker_normalizes_timeout_without_raising():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("scripted timeout", request=request)

    checker = HttpDestinationChecker(
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
        resolver=_public_resolver,
    )

    observations = checker.check(
        "https://jobs.example.test/original",
        sequence=1,
        attempt=1,
        stage=CheckStage.INITIAL_VALIDATION,
        observed_at="2026-08-30T12:00:00+00:00",
        employer_hosted=True,
    )

    assert len(observations) == 1
    assert observations[0].failure is NetworkFailure.TIMEOUT
    assert observations[0].status is None


def test_http_checker_marks_its_own_403_as_checker_only():
    checker = HttpDestinationChecker(
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(403, request=request)),
            follow_redirects=True,
        ),
        resolver=_public_resolver,
    )

    observation = checker.check(
        "https://jobs.example.test/original",
        sequence=1,
        attempt=1,
        stage=CheckStage.INITIAL_VALIDATION,
        observed_at="2026-08-30T12:00:00+00:00",
        employer_hosted=True,
    )[0]

    assert observation.status == 403
    assert observation.checker_only is True


def test_http_checker_blocks_resolved_private_destination_before_request():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, request=request)

    checker = HttpDestinationChecker(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=lambda _host: ("127.0.0.1",),
    )

    observation = checker.check(
        "https://community-target.example/role",
        sequence=1,
        attempt=1,
        stage=CheckStage.INITIAL_VALIDATION,
        observed_at="2026-08-30T12:00:00+00:00",
        employer_hosted=False,
    )[0]

    assert observation.failure is NetworkFailure.UNSAFE_DESTINATION
    assert calls == []


def test_http_checker_revalidates_and_blocks_private_redirect_target():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return httpx.Response(
            302,
            headers={"Location": "http://internal.example/admin"},
            request=request,
        )

    def resolver(host: str):
        return ("10.0.0.8",) if host == "internal.example" else ("93.184.216.34",)

    checker = HttpDestinationChecker(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=resolver,
    )

    observations = checker.check(
        "https://jobs.example.test/original",
        sequence=1,
        attempt=1,
        stage=CheckStage.INITIAL_VALIDATION,
        observed_at="2026-08-30T12:00:00+00:00",
        employer_hosted=True,
    )

    assert observations[0].status == 302
    assert observations[1].failure is NetworkFailure.UNSAFE_DESTINATION
    assert calls == ["jobs.example.test"]


def test_http_checker_stops_reading_after_bounded_response_body():
    class _LargeBody(httpx.SyncByteStream):
        def __iter__(self):
            yield b"apply now"
            yield b"x" * 100

    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=_LargeBody(), request=request)
        )
    )
    checker = HttpDestinationChecker(
        client=client,
        resolver=_public_resolver,
        max_body_bytes=12,
    )

    observation = checker.check(
        "https://jobs.example.test/original",
        sequence=1,
        attempt=1,
        stage=CheckStage.INITIAL_VALIDATION,
        observed_at="2026-08-30T12:00:00+00:00",
        employer_hosted=True,
    )[0]

    assert observation.status == 200
    assert observation.body == "apply nowxxx"
    assert len(observation.body.encode("utf-8")) <= 12
