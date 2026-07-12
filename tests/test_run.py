import json
from pathlib import Path

import httpx

from internshelper import db, review, run, store
from internshelper.config import Settings, SourceEntry
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
    def __init__(self, posts=None, exc=None):
        self._posts = posts or []
        self._exc = exc

    def fetch(self):
        if self._exc:
            raise self._exc
        return self._posts


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


# ---------- threshold notify ----------

def test_digest_fires_for_new_apply_first_and_stamps_exactly_once(tmp_path, monkeypatch):
    monkeypatch.setenv("INTERNSHELPER_COMPANIES", str(tmp_path / "companies.yaml"))
    c = _conn(tmp_path)
    e = SourceEntry(type="greenhouse", token="google")
    # Google is on the built-in dream list -> cold-start candidates land in apply_first
    posts = [_raw(f"greenhouse:{i}", e.source_key, company="Google") for i in range(3)]
    _wire(monkeypatch, {e.source_key: _FakeConnector(posts=posts)})
    send = _Send()
    res = _collect(c, tmp_path, _settings(), [e], "2026-06-18T12:00:00+00:00", send)
    assert res.sent is True and res.digested == 3 and res.inbox == 3
    assert len(send.calls) == 1
    assert "3 new Apply-first postings" in send.calls[0][0]
    assert "https://x/greenhouse:0" in send.calls[0][1]
    assert c.execute("SELECT COUNT(*) FROM postings WHERE notified_at IS NOT NULL").fetchone()[0] == 3
    nrun = c.execute("SELECT ok, count FROM runs WHERE source_key='notify'").fetchone()
    assert nrun["ok"] == 1 and nrun["count"] == 3

    # Second cycle, same postings -> already stamped, no duplicate digest
    _wire(monkeypatch, {e.source_key: _FakeConnector(posts=posts)})
    res2 = _collect(c, tmp_path, _settings(), [e], "2026-06-18T13:00:00+00:00", send)
    assert res2.sent is False and res2.digested == 0 and len(send.calls) == 1


def test_flagged_posting_is_not_digested(tmp_path, monkeypatch):
    monkeypatch.setenv("INTERNSHELPER_COMPANIES", str(tmp_path / "companies.yaml"))
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
    monkeypatch.setenv("INTERNSHELPER_COMPANIES", str(tmp_path / "companies.yaml"))
    c = _conn(tmp_path)
    e = SourceEntry(type="greenhouse", token="acme")
    _wire(monkeypatch, {e.source_key: _FakeConnector(
        posts=[_raw("greenhouse:1", e.source_key, company="Acme")])})
    send = _Send()
    res = _collect(c, tmp_path, _settings(), [e], "2026-06-18T12:00:00+00:00", send)
    assert res.sent is False and send.calls == []
    tier = c.execute("SELECT tier FROM postings WHERE posting_id='greenhouse:1'").fetchone()[0]
    assert tier == "everything_else"  # unlisted company: on the board, not in the email


def test_email_disabled_suppresses_digest_and_leaves_rows_unstamped(tmp_path, monkeypatch):
    monkeypatch.setenv("INTERNSHELPER_COMPANIES", str(tmp_path / "companies.yaml"))
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
