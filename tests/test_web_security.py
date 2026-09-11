"""HTTP-boundary protections for the loopback-only web UI."""

from __future__ import annotations

import sqlite3

import pytest

from internshelper import companies


@pytest.mark.parametrize(
    "path", ["/healthz", "/static/css/app.css", "/board/card/greenhouse:0"]
)
def test_nonloopback_host_is_rejected_globally(client, path):
    response = client.get(path, headers={"host": "attacker.example"})

    assert response.status_code == 403
    assert "Software Engineer Intern" not in response.text


def test_host_userinfo_cannot_disguise_a_nonlocal_authority(client):
    response = client.get(
        "/healthz", headers={"host": "attacker.example@localhost:8510"}
    )

    assert response.status_code == 403


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_every_unsafe_method_requires_browser_provenance_before_routing(client, method):
    response = client.request(method, "/healthz", headers={"origin": ""})

    assert response.status_code == 403


def test_cross_site_board_request_cannot_mutate_application_data(client, seeded_db):
    response = client.post(
        "/board/card/greenhouse:0",
        data={"status": "Applied", "applied_date": "", "notes": "private"},
        headers={
            "origin": "https://attacker.example",
            "sec-fetch-site": "cross-site",
        },
    )

    assert response.status_code == 403
    with sqlite3.connect(seeded_db) as conn:
        saved = conn.execute(
            "SELECT 1 FROM applications WHERE posting_id = ?", ("greenhouse:0",)
        ).fetchone()
    assert saved is None


def test_cross_site_company_request_cannot_mutate_private_config(client, companies_file):
    response = client.post(
        "/companies/add",
        data={"name": "Should Not Be Added"},
        headers={
            "origin": "https://attacker.example",
            "sec-fetch-site": "cross-site",
        },
    )

    assert response.status_code == 403
    entries, errors = companies.load_companies(companies_file)
    assert entries == []
    assert errors == []


@pytest.mark.parametrize("host", ["localhost:8510", "127.0.0.1:8510", "[::1]:8510"])
def test_loopback_gets_remain_available_without_browser_provenance(client, host):
    response = client.get("/healthz", headers={"host": host, "origin": ""})

    assert response.status_code == 200
    assert response.text == "ok"


def test_cross_origin_board_get_does_not_mutate_stale_tiers(client, seeded_db):
    with sqlite3.connect(seeded_db) as conn:
        conn.execute("UPDATE postings SET tier = NULL WHERE posting_id = ?", ("greenhouse:1",))

    response = client.get(
        "/board",
        headers={
            "origin": "https://attacker.example",
            "sec-fetch-site": "cross-site",
        },
    )

    assert response.status_code == 200
    with sqlite3.connect(seeded_db) as conn:
        tier = conn.execute(
            "SELECT tier FROM postings WHERE posting_id = ?", ("greenhouse:1",)
        ).fetchone()[0]
    assert tier is None


def test_same_origin_htmx_mutation_remains_available(client, seeded_db):
    response = client.post(
        "/board/dismiss",
        data={"posting_id": "greenhouse:1", "reason": "not relevant"},
        headers={
            "origin": "http://127.0.0.1:8510",
            "hx-request": "true",
            "sec-fetch-site": "same-origin",
        },
    )

    assert response.status_code == 200
    with sqlite3.connect(seeded_db) as conn:
        verdict = conn.execute(
            "SELECT verdict FROM postings WHERE posting_id = ?", ("greenhouse:1",)
        ).fetchone()[0]
    assert verdict == "no_match"


def test_same_origin_referer_is_accepted_when_origin_is_absent(client, seeded_db):
    response = client.post(
        "/board/flag",
        data={"posting_id": "greenhouse:1", "reason": "check"},
        headers={
            "origin": "",
            "referer": "http://127.0.0.1:8510/board",
            "sec-fetch-site": "same-origin",
        },
    )

    assert response.status_code == 200
    with sqlite3.connect(seeded_db) as conn:
        flagged_at = conn.execute(
            "SELECT flagged_at FROM postings WHERE posting_id = ?", ("greenhouse:1",)
        ).fetchone()[0]
    assert flagged_at is not None
