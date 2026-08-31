"""One offline collection-to-Board-to-confirmation acceptance path."""

from __future__ import annotations

from fastapi.testclient import TestClient

from internshelper import db, run, store
from internshelper.availability_checks import NetworkObservation
from internshelper.availability_service import verify_posting
from internshelper.availability_store import get_state
from internshelper.config import Settings, SourceEntry
from internshelper.models import Posting


NOW = "2026-08-30T12:00:00+00:00"


class _Connector:
    def __init__(self, posting):
        self.posting = posting

    def fetch(self):
        return [self.posting]


class _Checker:
    def check(self, url, **kwargs):
        return (
            NetworkObservation(
                sequence=kwargs["sequence"],
                observed_at=kwargs["observed_at"],
                stage=kwargs["stage"],
                target=kwargs["target"],
                attempt=kwargs["attempt"],
                status=404,
                body="Page not found",
                employer_hosted=kwargs["employer_hosted"],
            ),
        )


def test_temporary_collection_verify_and_confirm_path(tmp_path, monkeypatch):
    """No configured source, real database, email, or network endpoint is touched."""

    database = tmp_path / "availability-e2e.db"
    sources_path = tmp_path / "sources.yaml"
    companies_path = tmp_path / "companies.yaml"
    groups_path = tmp_path / "company-groups.yaml"
    sources_path.write_text("sources:\n", encoding="utf-8")
    companies_path.write_text("companies:\n", encoding="utf-8")
    groups_path.write_text("companies:\n", encoding="utf-8")
    monkeypatch.setenv("INTERNSHELPER_DB", str(database))
    monkeypatch.setenv("INTERNSHELPER_SOURCES", str(sources_path))
    monkeypatch.setenv("INTERNSHELPER_COMPANIES", str(companies_path))
    monkeypatch.setenv("INTERNSHELPER_COMPANY_GROUPS", str(groups_path))

    entry = SourceEntry(
        type="markdown", token="https://lists.example.test/README.md"
    )
    original = Posting(
        posting_id="markdown:e2e-original",
        source_key=entry.source_key,
        title="Software Engineering Intern",
        company="Example Co",
        url="https://jobs.example.test/original",
        is_internship=True,
        is_cs_relevant=True,
    )
    candidate = Posting(
        posting_id="greenhouse:e2e-candidate",
        source_key="greenhouse:example",
        title="Software Engineering Internship",
        company="Example Co",
        url="https://jobs.example.test/replacement",
        is_internship=True,
        is_cs_relevant=True,
    )
    monkeypatch.setattr(run, "build_connector", lambda _entry: _Connector(original))
    conn = db.connect(database)
    db.init_db(conn)
    settings = Settings(
        smtp_host="smtp.test",
        smtp_port=587,
        smtp_sender="sender@test",
        smtp_recipient="recipient@test",
        require_cs=True,
        require_intern_or_newgrad=True,
        keywords={
            "internship": ["intern"],
            "newgrad": ["new grad"],
            "cs": ["software"],
        },
    )
    run.run_cycle(
        conn,
        settings,
        [entry],
        now=NOW,
        password=None,
        payloads_dir=tmp_path / "payloads",
        email_enabled=False,
        availability_checker=_Checker(),
    )
    store.upsert(conn, candidate, now=NOW)
    conn.close()

    from internshelper.web import create_app

    with TestClient(create_app()) as client:
        board = client.get("/board")
        assert original.posting_id in board.text
        drawer = client.get(f"/board/card/{original.posting_id}")
        assert "Availability uncertain" in drawer.text
        assert "Verify and find application" in drawer.text

        def verify_offline(conn, posting_id, *, sources_path, now):
            return verify_posting(
                conn,
                posting_id,
                sources_path=sources_path,
                now=now,
                checker=_Checker(),
            )

        client.app.state.availability_verify = verify_offline
        verified = client.post(f"/board/card/{original.posting_id}/verify")
        assert verified.status_code == 200
        assert "Confirm replacement" in verified.text
        assert "Awaiting your confirmation" in verified.text
        confirmed = client.post(
            f"/board/card/{original.posting_id}/replacement/confirm"
        )
        assert confirmed.status_code == 200
        assert "Replacement confirmed" in confirmed.text

    conn = db.connect(database)
    state = get_state(conn, original.posting_id)
    stored_url = conn.execute(
        "SELECT url FROM postings WHERE posting_id = ?", (original.posting_id,)
    ).fetchone()["url"]
    conn.close()
    assert state["confirmed_url"] == candidate.url
    assert state["pending_candidate_url"] is None
    assert stored_url == original.url
