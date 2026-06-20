from internshelper import db, digest, store
from internshelper.config import Settings
from internshelper.models import Posting


def _settings(**kw):
    base = dict(
        smtp_host="smtp.test", smtp_port=587, smtp_sender="me@test", smtp_recipient="me@test",
        require_cs=True, require_intern_or_newgrad=True,
        keywords={"internship": ["intern"], "newgrad": ["new grad"], "cs": ["software"]},
    )
    base.update(kw)
    return Settings(**base)


def _conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.init_db(c)
    return c


def _match(pid, **kw):
    base = dict(posting_id=pid, source_key="greenhouse:stripe", title="SWE Intern",
                company="Stripe", url=f"https://x/{pid}", location="NYC",
                is_cs_relevant=True, is_internship=True)
    base.update(kw)
    return Posting(**base)


class _Recorder:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, settings, subject, html, password):
        if self.fail:
            raise RuntimeError("smtp down")
        self.calls.append((subject, html, password))


# ---------- render ----------

def test_render_subject_and_links(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _match("greenhouse:1", title="Backend Intern"), now="2026-06-18T10:00:00+00:00")
    rows = store.select_for_digest(c, "2026-06-18T00:00:00+00:00", True, True)
    subject, html = digest.render(rows)
    assert subject == "internsHELPer: 1 new role"
    assert "Backend Intern" in html
    assert "https://x/greenhouse:1" in html


# ---------- run_digest ----------

def test_cold_start_sends_nothing_and_sets_watermark(tmp_path):
    c = _conn(tmp_path)
    store.upsert(c, _match("greenhouse:1"), now="2026-06-18T10:00:00+00:00")
    send = _Recorder()
    res = digest.run_digest(c, _settings(), now="2026-06-18T12:00:00+00:00",
                            password="pw", send_fn=send)
    assert res.cold_start is True and res.sent == 0
    assert send.calls == []
    assert db.get_meta(c, "last_digest_at") == "2026-06-18T12:00:00+00:00"


def test_sends_only_new_matching_and_advances_watermark(tmp_path):
    c = _conn(tmp_path)
    db.set_meta(c, "last_digest_at", "2026-06-18T09:00:00+00:00")
    store.upsert(c, _match("greenhouse:new"), now="2026-06-18T10:00:00+00:00")
    store.upsert(c, _match("greenhouse:old"), now="2026-06-18T08:00:00+00:00")  # before watermark
    store.upsert(c, _match("greenhouse:csonly", is_internship=False),
                 now="2026-06-18T10:30:00+00:00")  # non-matching
    send = _Recorder()
    res = digest.run_digest(c, _settings(), now="2026-06-18T12:00:00+00:00",
                            password="pw", send_fn=send)
    assert res.ok and res.sent == 1
    subject, html, pw = send.calls[0]
    assert "greenhouse:new" in html and "greenhouse:old" not in html and "csonly" not in html
    assert db.get_meta(c, "last_digest_at") == "2026-06-18T12:00:00+00:00"


def test_send_failure_keeps_watermark_and_records_error_then_retries(tmp_path):
    c = _conn(tmp_path)
    db.set_meta(c, "last_digest_at", "2026-06-18T09:00:00+00:00")
    store.upsert(c, _match("greenhouse:new"), now="2026-06-18T10:00:00+00:00")

    failing = _Recorder(fail=True)
    res = digest.run_digest(c, _settings(), now="2026-06-18T12:00:00+00:00",
                            password="pw", send_fn=failing)
    assert res.ok is False and res.sent == 0
    assert db.get_meta(c, "last_digest_at") == "2026-06-18T09:00:00+00:00"  # unchanged
    err = c.execute("SELECT ok, error FROM runs WHERE source_key='digest'").fetchone()
    assert err["ok"] == 0 and "smtp down" in err["error"]

    # SMTP recovers next cycle -> the same posting is emitted once.
    ok_send = _Recorder()
    res2 = digest.run_digest(c, _settings(), now="2026-06-18T13:00:00+00:00",
                             password="pw", send_fn=ok_send)
    assert res2.sent == 1
    assert "greenhouse:new" in ok_send.calls[0][1]


def test_missing_password_with_rows_is_a_failure(tmp_path):
    c = _conn(tmp_path)
    db.set_meta(c, "last_digest_at", "2026-06-18T09:00:00+00:00")
    store.upsert(c, _match("greenhouse:new"), now="2026-06-18T10:00:00+00:00")
    send = _Recorder()
    res = digest.run_digest(c, _settings(), now="2026-06-18T12:00:00+00:00",
                            password=None, send_fn=send)
    assert res.ok is False and send.calls == []
    assert db.get_meta(c, "last_digest_at") == "2026-06-18T09:00:00+00:00"


# ---------- send() over a mocked smtplib ----------

def test_send_uses_smtp_login_and_send(monkeypatch):
    captured = {}

    class FakeSMTP:
        def __init__(self, host, port):
            captured["addr"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            captured["tls"] = True

        def login(self, user, pw):
            captured["login"] = (user, pw)

        def send_message(self, msg):
            captured["subject"] = msg["Subject"]
            captured["to"] = msg["To"]

    import smtplib
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    digest.send(_settings(), "Subj", "<b>hi</b>", "secret")
    assert captured["addr"] == ("smtp.test", 587)
    assert captured["tls"] is True
    assert captured["login"] == ("me@test", "secret")
    assert captured["subject"] == "Subj" and captured["to"] == "me@test"
