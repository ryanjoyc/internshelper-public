"""Shared fixtures for the web-UI test files (test_web*.py).

The web app resolves INTERNSHELPER_DB / INTERNSHELPER_SOURCES at create_app() time,
so these fixtures redirect both to tmp files before building the client.
"""

import pytest

from internshelper import db, store
from internshelper.models import Posting
from internshelper.review import set_verdict


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """Four postings: greenhouse:0 reviewed as match; greenhouse:1 + greenhouse:2 pending
    keyword-candidates (1 has an archived payload); greenhouse:plain pending non-candidate."""
    path = tmp_path / "d.db"
    monkeypatch.setenv("INTERNSHELPER_DB", str(path))
    payloads = tmp_path / "payloads"
    c = db.connect(path)
    db.init_db(c)
    for i in range(3):
        p = Posting(posting_id=f"greenhouse:{i}", source_key="greenhouse:stripe",
                    title="Software Engineer Intern", company="Stripe", url=f"https://x/{i}",
                    is_cs_relevant=True, is_internship=True)
        if i == 1:
            p.raw = {"content": "<p>Build <b>backend</b> systems</p>", "id": i}
        store.upsert(c, p, now="2026-06-18T10:00:00+00:00", payloads_dir=payloads)
    store.upsert(
        c,
        Posting(posting_id="greenhouse:plain", source_key="greenhouse:stripe",
                title="Line Cook", company="Bistro", url="https://x/plain"),
        now="2026-06-18T10:00:00+00:00",
        payloads_dir=payloads,
    )
    set_verdict(c, "greenhouse:0", "match", "looks good", now="2026-06-18T11:00:00+00:00")
    c.close()
    return path


@pytest.fixture
def sources_file(tmp_path, monkeypatch):
    p = tmp_path / "sources.yaml"
    p.write_text("sources:\n")
    monkeypatch.setenv("INTERNSHELPER_SOURCES", str(p))
    return p


@pytest.fixture
def client(seeded_db, sources_file):
    from fastapi.testclient import TestClient

    from internshelper.web import create_app

    with TestClient(create_app()) as c:  # context manager runs the lifespan (init_db)
        yield c
