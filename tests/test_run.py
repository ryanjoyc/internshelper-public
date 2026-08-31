import json
from pathlib import Path

import httpx

from internshelper import db, review, run, store
from internshelper.availability_checks import CheckStage, NetworkObservation
from internshelper.availability_store import evidence_history, get_projection, get_state
from internshelper.config import Settings, SourceEntry
from internshelper.connectors import FetchResult, build_connector
from internshelper.models import Posting


def _settings():
    return Settings(
        smtp_host="smtp.test", smtp_port=587, smtp_sender="me@test", smtp_recipient="me@test",
        require_cs=True, require_intern_or_newgrad=True,
        keywords={"internship": ["intern"], "newgrad": ["new grad"], "cs": ["software"]},
    )


def _conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.init_db(c)
    return c


class _FakeConnector:
    def __init__(self, posts=None, exc=None, *, complete=True):
        self._posts = posts or []
        self._exc = exc
        self._complete = complete
        self.diagnostics = []

    def fetch(self):
        if self._exc:
            raise self._exc
        return FetchResult(tuple(self._posts), complete=self._complete)


def _wire(monkeypatch, mapping):
    monkeypatch.setattr(run, "build_connector", lambda entry: mapping[entry.source_key])


class _Send:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, settings, subject, html, password):
        if self.fail:
            raise RuntimeError("smtp down")
        self.calls.append((subject, html))


class _AvailabilityChecker:
    def __init__(self, statuses, conn=None):
        self.statuses = list(statuses)
        self.conn = conn
        self.calls = []

    def check(self, url, **kwargs):
        if self.conn is not None:
            # A community row must already be pending/hidden when external validation starts.
            assert store.inbox_with_status(self.conn) == []
        self.calls.append((url, kwargs))
        status = self.statuses.pop(0)
        body = (
            "<h1>Software Engineering Intern</h1><button>Apply now</button>"
            if status == 200
            else "Page not found"
        )
        return (
            NetworkObservation(
                sequence=kwargs["sequence"],
                observed_at=kwargs["observed_at"],
                stage=kwargs["stage"],
                target=kwargs.get("target", "original_url"),
                attempt=kwargs["attempt"],
                status=status,
                body=body,
                employer_hosted=kwargs["employer_hosted"],
            ),
        )


def _raw(pid, source_key, title="Software Engineer Intern", company="C"):
    return Posting(posting_id=pid, source_key=source_key, title=title,
                   company=company, url=f"https://x/{pid}", raw={"id": pid, "title": title})


def _collect(c, tmp_path, settings, sources, now, send):
    return run.run_cycle(c, settings, sources, now=now, password="pw",
                         payloads_dir=tmp_path / "payloads", send_fn=send)


def test_heartbeat_written_at_start_even_with_zero_sources(tmp_path):
    c = _conn(tmp_path)
    _collect(c, tmp_path, _settings(), [], "2026-06-18T12:00:00+00:00", _Send())
    hb = c.execute("SELECT ok, count FROM runs WHERE source_key='run'").fetchall()
    assert len(hb) == 1 and hb[0]["ok"] == 1 and hb[0]["count"] == 0


def test_collect_stores_pending_with_payload_and_flags(tmp_path, monkeypatch):
    c = _conn(tmp_path)
    e = SourceEntry(type="greenhouse", token="stripe")
    _wire(monkeypatch, {e.source_key: _FakeConnector(posts=[_raw("greenhouse:1", e.source_key)])})
    _collect(c, tmp_path, _settings(), [e], "2026-06-18T12:00:00+00:00", _Send())
    row = c.execute("SELECT review_status, payload_path, is_cs_relevant, is_internship "
                    "FROM postings WHERE posting_id='greenhouse:1'").fetchone()
    assert row["review_status"] == "pending"
    assert row["is_cs_relevant"] == 1 and row["is_internship"] == 1
    assert json.loads(Path(row["payload_path"]).read_text())["id"] == "greenhouse:1"


def test_per_source_title_filter_drops_nonmatching(tmp_path, monkeypatch):
    c = _conn(tmp_path)
    e = SourceEntry(type="greenhouse", token="bigco", title_must_match=["engineer"])
    _wire(monkeypatch, {e.source_key: _FakeConnector(posts=[
        _raw("greenhouse:eng", e.source_key, "Software Engineer Intern"),
        _raw("greenhouse:cashier", e.source_key, "Cashier"),
    ])})
    _collect(c, tmp_path, _settings(), [e], "t", _Send())
    ids = [r["posting_id"] for r in c.execute("SELECT posting_id FROM postings")]
    assert ids == ["greenhouse:eng"]  # 'Cashier' dropped before store


def test_flood_guard_drops_are_recorded(tmp_path, monkeypatch):
    c = _conn(tmp_path)
    e = SourceEntry(type="greenhouse", token="bigco", title_must_match=["engineer"])
    _wire(monkeypatch, {e.source_key: _FakeConnector(posts=[
        _raw("greenhouse:eng", e.source_key, "Software Engineer Intern"),
        _raw("greenhouse:cashier", e.source_key, "Cashier"),
    ])})
    _collect(c, tmp_path, _settings(), [e], "t", _Send())
    row = c.execute("SELECT count, dropped FROM runs WHERE source_key=?",
                    (e.source_key,)).fetchone()
    assert row["count"] == 1 and row["dropped"] == 1


def test_failure_isolation_one_source_raises(tmp_path, monkeypatch):
    c = _conn(tmp_path)
    a = SourceEntry(type="greenhouse", token="aaa")
    b = SourceEntry(type="lever", token="bbb")
    store.upsert(c, _raw("greenhouse:old", a.source_key), now="2026-06-18T08:00:00+00:00")
    _wire(monkeypatch, {
        a.source_key: _FakeConnector(exc=RuntimeError("boom")),
        b.source_key: _FakeConnector(posts=[_raw("lever:1", b.source_key)]),
    })
    _collect(c, tmp_path, _settings(), [a, b], "2026-06-18T12:00:00+00:00", _Send())
    arun = c.execute("SELECT ok, error FROM runs WHERE source_key=?", (a.source_key,)).fetchone()
    assert arun["ok"] == 0 and "boom" in arun["error"]
    assert c.execute("SELECT 1 FROM postings WHERE posting_id='lever:1'").fetchone()
    assert c.execute("SELECT is_active FROM postings WHERE posting_id='greenhouse:old'").fetchone()["is_active"] == 1


def test_timeout_recorded_and_does_not_close(tmp_path, monkeypatch):
    c = _conn(tmp_path)
    a = SourceEntry(type="greenhouse", token="aaa")
    store.upsert(c, _raw("greenhouse:old", a.source_key), now="2026-06-18T08:00:00+00:00")
    _wire(monkeypatch, {a.source_key: _FakeConnector(exc=httpx.ReadTimeout("slow"))})
    _collect(c, tmp_path, _settings(), [a], "2026-06-18T12:00:00+00:00", _Send())
    arun = c.execute("SELECT ok, error FROM runs WHERE source_key=?", (a.source_key,)).fetchone()
    assert arun["ok"] == 0 and "timeout" in arun["error"].lower()
    assert c.execute("SELECT is_active FROM postings WHERE posting_id='greenhouse:old'").fetchone()["is_active"] == 1


def test_community_destination_is_pending_before_first_display(tmp_path, monkeypatch):
    c = _conn(tmp_path)
    entry = SourceEntry(type="markdown", token="https://lists.example.test/README.md")
    posting = _raw("markdown:1", entry.source_key)
    _wire(monkeypatch, {entry.source_key: _FakeConnector(posts=[posting])})
    checker = _AvailabilityChecker([200], conn=c)

    run.run_cycle(
        c,
        _settings(),
        [entry],
        now="2026-06-18T12:00:00+00:00",
        password="pw",
        payloads_dir=tmp_path / "payloads",
        send_fn=_Send(),
        availability_checker=checker,
    )

    assert len(checker.calls) == 1
    assert store.inbox_with_status(c)[0]["availability"] == "live"
    assert get_state(c, posting.posting_id)["validation_completed"] == 1


def test_community_destination_does_not_inherit_employer_host_authority(
    tmp_path, monkeypatch
):
    c = _conn(tmp_path)
    entry = SourceEntry(type="markdown", token="https://lists.example.test/README.md")
    posting = _raw("markdown:closure-copy", entry.source_key)
    _wire(monkeypatch, {entry.source_key: _FakeConnector(posts=[posting])})

    class CommunityChecker:
        def __init__(self):
            self.host_authority = []

        def check(self, url, **kwargs):
            self.host_authority.append(kwargs["employer_hosted"])
            return (
                NetworkObservation(
                    sequence=kwargs["sequence"],
                    observed_at=kwargs["observed_at"],
                    stage=kwargs["stage"],
                    target=kwargs["target"],
                    attempt=kwargs["attempt"],
                    status=200,
                    body="This job is no longer accepting applications",
                    employer_hosted=kwargs["employer_hosted"],
                ),
            )

    checker = CommunityChecker()
    run.run_cycle(
        c, _settings(), [entry], "2026-06-18T12:00:00+00:00", "pw",
        tmp_path / "payloads", send_fn=_Send(),
        availability_checker=checker,
    )

    assert checker.host_authority == [False]
    assert get_projection(c, posting.posting_id).decision.availability.value == "uncertain"


def test_community_insert_is_not_visible_to_another_connection_before_pending(
    tmp_path, monkeypatch
):
    c = _conn(tmp_path)
    entry = SourceEntry(type="markdown", token="https://lists.example.test/README.md")
    posting = _raw("markdown:atomic", entry.source_key)
    _wire(monkeypatch, {entry.source_key: _FakeConnector(posts=[posting])})
    checker = _AvailabilityChecker([200])
    real_ensure_pending = run.ensure_pending

    def observe_then_mark_pending(conn, posting_id, authority, *, now):
        observer = db.connect(tmp_path / "t.db")
        try:
            assert observer.execute(
                "SELECT 1 FROM postings WHERE posting_id = ?", (posting_id,)
            ).fetchone() is None
            assert store.inbox_with_status(observer) == []
        finally:
            observer.close()
        return real_ensure_pending(conn, posting_id, authority, now=now)

    monkeypatch.setattr(run, "ensure_pending", observe_then_mark_pending)
    run.run_cycle(
        c,
        _settings(),
        [entry],
        now="2026-06-18T12:00:00+00:00",
        password="pw",
        payloads_dir=tmp_path / "payloads",
        send_fn=_Send(),
        availability_checker=checker,
    )

    assert store.inbox_with_status(c)[0]["posting_id"] == posting.posting_id


def test_scheduled_collection_retry_uses_a_distinct_attempt(tmp_path, monkeypatch):
    c = _conn(tmp_path)
    entry = SourceEntry(type="greenhouse", token="example")
    posting = _raw("greenhouse:retry", entry.source_key)
    checker = _AvailabilityChecker([404, 404])
    _wire(monkeypatch, {entry.source_key: _FakeConnector(posts=[posting])})

    first = run.run_cycle(
        c,
        _settings(),
        [entry],
        now="2026-06-18T12:00:00+00:00",
        password="pw",
        payloads_dir=tmp_path / "payloads",
        send_fn=_Send(),
        availability_checker=checker,
    )
    second = run.run_cycle(
        c,
        _settings(),
        [entry],
        now="2026-06-18T13:00:00+00:00",
        password="pw",
        payloads_dir=tmp_path / "payloads",
        send_fn=_Send(),
        availability_checker=checker,
    )

    assert first.inbox == 1
    assert second.inbox == 0
    assert [row["attempt"] for row in evidence_history(c, posting.posting_id)] == [1, 2]
    assert [call[1]["stage"] for call in checker.calls] == [
        CheckStage.INITIAL_VALIDATION,
        CheckStage.SCHEDULED_RETRY,
    ]


def test_closed_posting_stays_closed_until_the_destination_recovers(tmp_path, monkeypatch):
    c = _conn(tmp_path)
    entry = SourceEntry(type="greenhouse", token="example")
    posting = _raw("greenhouse:stable-close", entry.source_key)
    checker = _AvailabilityChecker([404, 404, 404, 200])
    _wire(monkeypatch, {entry.source_key: _FakeConnector(posts=[posting])})

    for hour in (12, 13, 14):
        run.run_cycle(
            c, _settings(), [entry], f"2026-06-18T{hour}:00:00+00:00", "pw",
            tmp_path / "payloads", send_fn=_Send(), availability_checker=checker,
        )
    assert get_projection(c, posting.posting_id).decision.availability.value == "closed"

    run.run_cycle(
        c, _settings(), [entry], "2026-06-18T15:00:00+00:00", "pw",
        tmp_path / "payloads", send_fn=_Send(), availability_checker=checker,
    )
    assert get_projection(c, posting.posting_id).decision.availability.value == "live"


def test_complete_ats_absence_is_uncertain_once_and_closed_twice(tmp_path, monkeypatch):
    c = _conn(tmp_path)
    entry = SourceEntry(type="greenhouse", token="example")
    original = _raw("greenhouse:original", entry.source_key)
    other = _raw("greenhouse:other", entry.source_key)
    checker = _AvailabilityChecker([200, 200])

    _wire(monkeypatch, {entry.source_key: _FakeConnector(posts=[original])})
    run.run_cycle(
        c, _settings(), [entry], "2026-06-18T12:00:00+00:00", "pw",
        tmp_path / "payloads", send_fn=_Send(), availability_checker=checker,
    )
    _wire(monkeypatch, {entry.source_key: _FakeConnector(posts=[other])})
    run.run_cycle(
        c, _settings(), [entry], "2026-06-18T13:00:00+00:00", "pw",
        tmp_path / "payloads", send_fn=_Send(), availability_checker=checker,
    )

    once = get_projection(c, original.posting_id)
    assert once.decision.availability.value == "uncertain"
    assert original.posting_id in {row["posting_id"] for row in store.inbox_with_status(c)}

    run.run_cycle(
        c, _settings(), [entry], "2026-06-18T14:00:00+00:00", "pw",
        tmp_path / "payloads", send_fn=_Send(), availability_checker=checker,
    )

    twice = get_projection(c, original.posting_id)
    assert twice.decision.availability.value == "closed"
    assert original.posting_id not in {row["posting_id"] for row in store.inbox_with_status(c)}


def test_empty_ats_enumeration_without_completeness_proof_does_not_close(
    tmp_path, monkeypatch
):
    c = _conn(tmp_path)
    entry = SourceEntry(type="greenhouse", token="example")
    original = _raw("greenhouse:only-role", entry.source_key)
    checker = _AvailabilityChecker([200])

    _wire(monkeypatch, {entry.source_key: _FakeConnector(posts=[original])})
    run.run_cycle(
        c, _settings(), [entry], "2026-06-18T12:00:00+00:00", "pw",
        tmp_path / "payloads", send_fn=_Send(), availability_checker=checker,
    )
    _wire(monkeypatch, {entry.source_key: _FakeConnector(posts=[])})
    run.run_cycle(
        c, _settings(), [entry], "2026-06-18T13:00:00+00:00", "pw",
        tmp_path / "payloads", send_fn=_Send(), availability_checker=checker,
    )
    assert get_projection(c, original.posting_id).decision.availability.value == "live"

    run.run_cycle(
        c, _settings(), [entry], "2026-06-18T14:00:00+00:00", "pw",
        tmp_path / "payloads", send_fn=_Send(), availability_checker=checker,
    )
    assert get_projection(c, original.posting_id).decision.availability.value == "live"
    assert not any(
        row["signal"] == "first_party_absent"
        for row in evidence_history(c, original.posting_id)
    )


def test_workday_partial_parse_never_advances_absence_or_close_detection(
    tmp_path, monkeypatch
):
    """A fetched-but-unparseable Workday row is not proof that the role disappeared."""

    c = _conn(tmp_path)
    entry = SourceEntry(
        type="workday",
        token="https://example.wd1.myworkdayjobs.com/Internships",
        label="Example Co",
    )
    original_item = {
        "title": "Software Engineering Intern",
        "externalPath": "/job/Seattle/Software-Engineering-Intern_R-100",
        "locationsText": "Seattle, WA",
    }
    other_item = {
        "title": "Data Science Intern",
        "externalPath": "/job/New-York/Data-Science-Intern_R-200",
        "locationsText": "New York, NY",
    }

    def connector_for(items):
        connector = build_connector(entry)
        page = {"total": len(items), "jobPostings": items}
        monkeypatch.setattr(connector, "_post_json", lambda _url, _body: page)
        return connector

    complete = connector_for([original_item])
    original_id = complete.parse([{"jobPostings": [original_item]}])[0].posting_id
    other_id = complete.parse([{"jobPostings": [other_item]}])[0].posting_id
    _wire(monkeypatch, {entry.source_key: complete})
    checker = _AvailabilityChecker([200, 200])
    run.run_cycle(
        c, _settings(), [entry], "2026-06-18T12:00:00+00:00", "pw",
        tmp_path / "payloads", send_fn=_Send(), availability_checker=checker,
    )

    malformed_original = {"title": original_item["title"]}
    for hour in (13, 14, 15):
        _wire(
            monkeypatch,
            {entry.source_key: connector_for([malformed_original, other_item])},
        )
        run.run_cycle(
            c, _settings(), [entry], f"2026-06-18T{hour}:00:00+00:00", "pw",
            tmp_path / "payloads", send_fn=_Send(), availability_checker=checker,
        )

        projection = get_projection(c, original_id)
        assert projection.decision.availability.value == "live"
        assert projection.visible is True
        assert c.execute(
            "SELECT is_active FROM postings WHERE posting_id = ?", (original_id,)
        ).fetchone()["is_active"] == 1
        assert get_projection(c, other_id).visible is True
        assert not any(
            row["signal"] == "first_party_absent"
            for row in evidence_history(c, original_id)
        )


def test_title_guard_drop_is_not_interpreted_as_first_party_absence(
    tmp_path, monkeypatch
):
    c = _conn(tmp_path)
    entry = SourceEntry(
        type="greenhouse", token="example", title_must_match=["intern"]
    )
    original = _raw(
        "greenhouse:guarded", entry.source_key, title="Software Engineer Intern"
    )
    checker = _AvailabilityChecker([200])
    _wire(monkeypatch, {entry.source_key: _FakeConnector(posts=[original])})
    run.run_cycle(
        c, _settings(), [entry], "2026-06-18T12:00:00+00:00", "pw",
        tmp_path / "payloads", send_fn=_Send(), availability_checker=checker,
    )

    filtered = _raw("greenhouse:guarded", entry.source_key, title="Software Engineer")
    _wire(monkeypatch, {entry.source_key: _FakeConnector(posts=[filtered])})
    for hour in (13, 14):
        run.run_cycle(
            c, _settings(), [entry], f"2026-06-18T{hour}:00:00+00:00", "pw",
            tmp_path / "payloads", send_fn=_Send(), availability_checker=checker,
        )

    projection = get_projection(c, original.posting_id)
    assert projection.decision.availability.value == "live"
    assert not any(
        row["signal"] == "first_party_absent"
        for row in evidence_history(c, original.posting_id)
    )


def test_repeated_community_removal_preserves_destination_failure_attempts(
    tmp_path, monkeypatch
):
    c = _conn(tmp_path)
    entry = SourceEntry(type="markdown", token="https://lists.example.test/README.md")
    original = _raw("markdown:only-role", entry.source_key)
    other = _raw("markdown:other-role", entry.source_key, title="Data Science Intern")
    checker = _AvailabilityChecker([200, 404, 404])
    store.upsert(c, other, now="2026-06-18T11:00:00+00:00")

    _wire(monkeypatch, {entry.source_key: _FakeConnector(posts=[original])})
    run.run_cycle(
        c, _settings(), [entry], "2026-06-18T12:00:00+00:00", "pw",
        tmp_path / "payloads", send_fn=_Send(), availability_checker=checker,
    )
    _wire(monkeypatch, {entry.source_key: _FakeConnector(posts=[other])})
    run.run_cycle(
        c, _settings(), [entry], "2026-06-18T13:00:00+00:00", "pw",
        tmp_path / "payloads", send_fn=_Send(), availability_checker=checker,
    )
    assert get_projection(c, original.posting_id).decision.availability.value == "uncertain"

    run.run_cycle(
        c, _settings(), [entry], "2026-06-18T14:00:00+00:00", "pw",
        tmp_path / "payloads", send_fn=_Send(), availability_checker=checker,
    )
    projection = get_projection(c, original.posting_id)
    assert projection.decision.availability.value == "closed"
    hard_attempts = [
        row["attempt"]
        for row in evidence_history(c, original.posting_id)
        if row["signal"] == "http_404"
    ]
    assert hard_attempts == [2, 3]


def test_community_removal_checker_failure_is_isolated(tmp_path, monkeypatch):
    c = _conn(tmp_path)
    entry = SourceEntry(type="markdown", token="https://lists.example.test/README.md")
    original = _raw("markdown:only-role", entry.source_key)
    other = _raw("markdown:other-role", entry.source_key, title="Data Science Intern")
    store.upsert(c, other, now="2026-06-18T11:00:00+00:00")

    class Checker:
        def __init__(self):
            self.calls = 0

        def check(self, url, **kwargs):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("checker unavailable")
            return _AvailabilityChecker([200]).check(url, **kwargs)

    checker = Checker()
    _wire(monkeypatch, {entry.source_key: _FakeConnector(posts=[original])})
    run.run_cycle(
        c, _settings(), [entry], "2026-06-18T12:00:00+00:00", "pw",
        tmp_path / "payloads", send_fn=_Send(), availability_checker=checker,
    )
    _wire(monkeypatch, {entry.source_key: _FakeConnector(posts=[other])})

    run.run_cycle(
        c, _settings(), [entry], "2026-06-18T13:00:00+00:00", "pw",
        tmp_path / "payloads", send_fn=_Send(), availability_checker=checker,
    )

    assert get_projection(c, original.posting_id).decision.availability.value == "uncertain"
    error = c.execute(
        "SELECT error FROM runs WHERE source_key = ? ORDER BY id DESC LIMIT 1",
        (f"availability:{original.posting_id}",),
    ).fetchone()
    assert error is not None and "checker unavailable" in error["error"]


def test_duplicate_sources_reconcile_live_and_repeated_absence_as_uncertain(
    tmp_path, monkeypatch
):
    c = _conn(tmp_path)
    greenhouse = SourceEntry(type="greenhouse", token="example")
    lever = SourceEntry(type="lever", token="example")
    shared_url = "https://jobs.example.test/roles/shared"
    first_party_live = _raw("greenhouse:shared", greenhouse.source_key)
    first_party_live.url = shared_url
    later_absent = _raw("lever:shared", lever.source_key)
    later_absent.url = shared_url
    other = _raw("lever:other", lever.source_key, title="Data Science Intern")
    checker = _AvailabilityChecker([200, 200, 200])

    _wire(
        monkeypatch,
        {
            greenhouse.source_key: _FakeConnector(posts=[first_party_live]),
            lever.source_key: _FakeConnector(posts=[later_absent]),
        },
    )
    run.run_cycle(
        c, _settings(), [greenhouse, lever], "2026-06-18T12:00:00+00:00", "pw",
        tmp_path / "payloads", send_fn=_Send(), availability_checker=checker,
    )
    duplicate = c.execute(
        "SELECT duplicate_of FROM postings WHERE posting_id = ?",
        (later_absent.posting_id,),
    ).fetchone()
    assert duplicate["duplicate_of"] == first_party_live.posting_id

    _wire(
        monkeypatch,
        {
            greenhouse.source_key: _FakeConnector(posts=[first_party_live]),
            lever.source_key: _FakeConnector(posts=[other]),
        },
    )
    for hour in (13, 14):
        run.run_cycle(
            c, _settings(), [greenhouse, lever],
            f"2026-06-18T{hour}:00:00+00:00", "pw",
            tmp_path / "payloads", send_fn=_Send(), availability_checker=checker,
        )

    projection = get_projection(c, first_party_live.posting_id)
    assert projection.decision.availability.value == "uncertain"
    assert projection.decision.primary_action.value == "apply"
    assert projection.decision.secondary_action.value == "verify"
    assert c.execute(
        "SELECT url FROM postings WHERE posting_id = ?", (first_party_live.posting_id,)
    ).fetchone()["url"] == shared_url
    assert any(
        row["is_current"] and row["signal"] == "trustworthy_conflict"
        for row in evidence_history(c, first_party_live.posting_id)
    )


# ---------- threshold notify ----------

def test_digest_fires_for_new_top_target_and_stamps_exactly_once(tmp_path, monkeypatch):
    groups = tmp_path / "company-groups.yaml"
    groups.write_text("companies:\n  - name: Google\n    group: top_target\n")
    monkeypatch.setenv("INTERNSHELPER_COMPANY_GROUPS", str(groups))
    c = _conn(tmp_path)
    e = SourceEntry(type="greenhouse", token="google")
    # Google is explicitly configured as a top target.
    posts = [_raw(f"greenhouse:{i}", e.source_key, company="Google") for i in range(3)]
    _wire(monkeypatch, {e.source_key: _FakeConnector(posts=posts)})
    send = _Send()
    res = _collect(c, tmp_path, _settings(), [e], "2026-06-18T12:00:00+00:00", send)
    assert res.sent is True and res.digested == 3 and res.inbox == 3
    assert len(send.calls) == 1
    assert "3 new Top-target postings" in send.calls[0][0]
    assert "https://x/greenhouse:0" in send.calls[0][1]
    assert c.execute("SELECT COUNT(*) FROM postings WHERE notified_at IS NOT NULL").fetchone()[0] == 3
    nrun = c.execute("SELECT ok, count FROM runs WHERE source_key='notify'").fetchone()
    assert nrun["ok"] == 1 and nrun["count"] == 3

    # Second cycle, same postings -> already stamped, no duplicate digest
    _wire(monkeypatch, {e.source_key: _FakeConnector(posts=posts)})
    res2 = _collect(c, tmp_path, _settings(), [e], "2026-06-18T13:00:00+00:00", send)
    assert res2.sent is False and res2.digested == 0 and len(send.calls) == 1


def test_flagged_posting_is_not_digested(tmp_path, monkeypatch):
    groups = tmp_path / "company-groups.yaml"
    groups.write_text("companies:\n  - name: Google\n    group: top_target\n")
    monkeypatch.setenv("INTERNSHELPER_COMPANY_GROUPS", str(groups))
    c = _conn(tmp_path)
    e = SourceEntry(type="greenhouse", token="google")
    posts = [_raw("greenhouse:1", e.source_key, company="Google")]
    # already collected + flagged as suspect before the digest cycle runs
    store.upsert(c, posts[0], now="2026-06-18T08:00:00+00:00")
    review.flag(c, "greenhouse:1", "link shows nothing", now="2026-06-18T09:00:00+00:00")
    _wire(monkeypatch, {e.source_key: _FakeConnector(posts=posts)})
    send = _Send()
    res = _collect(c, tmp_path, _settings(), [e], "2026-06-18T12:00:00+00:00", send)
    # a dream-company row, but flagged: never emailed (and left unstamped for later)
    assert res.sent is False and res.digested == 0 and send.calls == []
    assert c.execute("SELECT notified_at FROM postings WHERE posting_id='greenhouse:1'"
                     ).fetchone()[0] is None


def test_no_digest_for_lower_tiers(tmp_path, monkeypatch):
    monkeypatch.setenv("INTERNSHELPER_COMPANY_GROUPS", str(tmp_path / "groups.yaml"))
    c = _conn(tmp_path)
    e = SourceEntry(type="greenhouse", token="acme")
    _wire(monkeypatch, {e.source_key: _FakeConnector(
        posts=[_raw("greenhouse:1", e.source_key, company="Acme")])})
    send = _Send()
    res = _collect(c, tmp_path, _settings(), [e], "2026-06-18T12:00:00+00:00", send)
    assert res.sent is False and send.calls == []
    tier = c.execute("SELECT tier FROM postings WHERE posting_id='greenhouse:1'").fetchone()[0]
    assert tier == "unclassified"  # unlisted company: on the board, not in the email


def test_email_disabled_suppresses_digest_and_leaves_rows_unstamped(tmp_path, monkeypatch):
    groups = tmp_path / "company-groups.yaml"
    groups.write_text("companies:\n  - name: Google\n    group: top_target\n")
    monkeypatch.setenv("INTERNSHELPER_COMPANY_GROUPS", str(groups))
    c = _conn(tmp_path)
    e = SourceEntry(type="greenhouse", token="google")
    posts = [_raw(f"greenhouse:{i}", e.source_key, company="Google") for i in range(3)]
    _wire(monkeypatch, {e.source_key: _FakeConnector(posts=posts)})
    send = _Send()
    res = run.run_cycle(c, _settings(), [e], now="2026-06-18T12:00:00+00:00",
                        password="pw", payloads_dir=tmp_path / "payloads",
                        send_fn=send, email_enabled=False)
    assert res.sent is False and send.calls == []
    assert c.execute("SELECT 1 FROM runs WHERE source_key='notify'").fetchone() is None
    # collection still happened; rows stay un-notified so enabling email digests them later
    assert c.execute("SELECT COUNT(*) FROM postings WHERE notified_at IS NULL").fetchone()[0] == 3


def test_collect_disabled_skips_fetch(tmp_path, monkeypatch):
    monkeypatch.setenv("INTERNSHELPER_FEATURE_COLLECT", "0")
    called = []
    monkeypatch.setattr(run, "build_connector", lambda e: called.append(e) or _FakeConnector())
    assert run.main([]) == 0
    assert called == []


def test_main_end_to_end_collects_surfaces_config_error_and_honors_email_flag(tmp_path, monkeypatch):
    db_file = tmp_path / "t.db"
    src = tmp_path / "sources.yaml"
    settings = tmp_path / "settings.toml"
    src.write_text("sources:\n  - type: greenhouse\n    token: stripe\n  - type: martian\n    token: x\n")
    settings.write_text('[smtp]\nhost="smtp.test"\n'
                        '[keywords]\ninternship=["intern"]\nnewgrad=["new grad"]\ncs=["software"]\n')
    monkeypatch.setenv("INTERNSHELPER_DB", str(db_file))
    monkeypatch.setenv("INTERNSHELPER_SOURCES", str(src))
    monkeypatch.setenv("INTERNSHELPER_SETTINGS", str(settings))
    monkeypatch.setenv("INTERNSHELPER_FEATURE_EMAIL", "0")   # below-the-flag path through main()
    monkeypatch.setenv("INTERNSHELPER_FEATURE_AVAILABILITY", "0")
    monkeypatch.delenv("INTERNSHELPER_FEATURE_COLLECT", raising=False)
    monkeypatch.setattr(run, "build_connector",
                        lambda entry: _FakeConnector(posts=[_raw("greenhouse:1", "greenhouse:stripe")]))
    assert run.main([]) == 0
    c = db.connect(db_file)
    db.init_db(c)
    assert c.execute("SELECT 1 FROM postings WHERE posting_id='greenhouse:1'").fetchone()    # collected
    assert c.execute("SELECT 1 FROM runs WHERE source_key='config' AND ok=0").fetchone()     # malformed surfaced
    assert c.execute("SELECT 1 FROM runs WHERE source_key='notify'").fetchone() is None      # EMAIL=0 honored


def test_digest_send_failure_leaves_rows_unstamped_for_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("INTERNSHELPER_COMPANIES", str(tmp_path / "companies.yaml"))
    c = _conn(tmp_path)
    e = SourceEntry(type="greenhouse", token="google")
    posts = [_raw(f"greenhouse:{i}", e.source_key, company="Google") for i in range(3)]
    _wire(monkeypatch, {e.source_key: _FakeConnector(posts=posts)})
    failing = _Send(fail=True)
    res = _collect(c, tmp_path, _settings(), [e], "2026-06-18T12:00:00+00:00", failing)
    assert res.sent is False and res.digested == 0
    assert c.execute("SELECT COUNT(*) FROM postings WHERE notified_at IS NULL").fetchone()[0] == 3
    nrun = c.execute("SELECT ok, error FROM runs WHERE source_key='notify'").fetchone()
    assert nrun["ok"] == 0 and "smtp down" in nrun["error"]

    # next cycle with a working sender picks the same rows up
    _wire(monkeypatch, {e.source_key: _FakeConnector(posts=posts)})
    working = _Send()
    res2 = _collect(c, tmp_path, _settings(), [e], "2026-06-18T13:00:00+00:00", working)
    assert res2.sent is True and res2.digested == 3


def test_run_cycle_rescores_new_pending_rows(tmp_path, monkeypatch):
    from internshelper import review

    c = _conn(tmp_path)
    # Enough interleaved strong history to train the ranker.
    for i in range(20):
        store.upsert(c, Posting(posting_id=f"m:{i}", source_key="greenhouse:stripe",
                                title="Quant Intern", company="C", url=f"https://x/m{i}"),
                     now="2026-06-18T10:00:00+00:00")
        review.set_verdict(c, f"m:{i}", "match", "yes", now=f"2026-06-18T10:{i:02d}:00+00:00")
    for i in range(15):
        store.upsert(c, Posting(posting_id=f"n:{i}", source_key="greenhouse:stripe",
                                title="Sales Manager", company="C", url=f"https://x/n{i}"),
                     now="2026-06-18T10:00:00+00:00")
        review.set_verdict(c, f"n:{i}", "no_match", "no", now=f"2026-06-18T11:{i:02d}:00+00:00")

    e = SourceEntry(type="greenhouse", token="stripe")
    _wire(monkeypatch, {e.source_key: _FakeConnector(posts=[_raw("greenhouse:new", e.source_key)])})
    res = _collect(c, tmp_path, _settings(), [e], "2026-06-18T12:00:00+00:00", _Send())

    assert res.rescored == 21  # inbox scope: 20 old matches + the new arrival
    row = c.execute("SELECT rank_score FROM postings WHERE posting_id='greenhouse:new'").fetchone()
    assert row["rank_score"] is not None


def test_run_cycle_survives_a_raising_rescorer(tmp_path, monkeypatch):
    from internshelper import ranking

    c = _conn(tmp_path)
    e = SourceEntry(type="greenhouse", token="stripe")
    _wire(monkeypatch, {e.source_key: _FakeConnector(posts=[_raw("greenhouse:1", e.source_key)])})

    def boom(conn, now):
        raise RuntimeError("ranker broke")

    monkeypatch.setattr(ranking, "rescore_inbox", boom)
    res = _collect(c, tmp_path, _settings(), [e], "2026-06-18T12:00:00+00:00", _Send())

    assert res.rescored == 0  # cycle completed anyway
    rank_run = c.execute("SELECT ok, error FROM runs WHERE source_key='rank'").fetchone()
    assert rank_run["ok"] == 0 and "ranker broke" in rank_run["error"]
