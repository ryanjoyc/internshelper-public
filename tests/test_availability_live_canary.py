"""Optional read-only availability probes; observations are never acceptance assertions."""

import httpx
import pytest

from availability.corpus import load_corpus


pytestmark = pytest.mark.live_canary
CANARIES = load_corpus()["live_canaries"]


@pytest.mark.parametrize("canary", CANARIES, ids=lambda canary: canary["id"])
def test_live_canary_reports_observation(canary):
    assert canary["mutates"] is False
    try:
        with httpx.Client(follow_redirects=True, timeout=10) as client:
            response = client.request(
                canary["method"],
                canary["url"],
                json=canary.get("json"),
                headers={"User-Agent": "internsHELPer availability canary/1.0"},
            )
    except httpx.HTTPError as exc:
        pytest.xfail(f"informational canary could not connect: {type(exc).__name__}: {exc}")
    print(
        f"{canary['id']}: status={response.status_code} final_url={response.url} "
        f"history={[item.status_code for item in response.history]}"
    )
    # Any HTTP response is useful canary data. Product acceptance never depends on its status.
