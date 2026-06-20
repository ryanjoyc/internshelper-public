import pytest

from internshelper import notify
from internshelper.config import Settings


def _settings():
    return Settings(
        smtp_host="smtp.test", smtp_port=587, smtp_sender="me@test", smtp_recipient="me@test",
        require_cs=True, require_intern_or_newgrad=True,
        keywords={"internship": ["intern"], "newgrad": ["new grad"], "cs": ["software"]},
    )


def test_render_nudge_subject_and_breakdown():
    subject, html = notify.render_nudge(12, 5)
    assert subject == "internsHELPer: 12 postings ready to review"
    assert "5 keyword-candidate" in html and "7 other" in html
    assert "/review-internships" in html


def test_send_nudge_missing_password_raises():
    with pytest.raises(RuntimeError):
        notify.send_nudge(_settings(), 5, 2, password=None, send_fn=lambda *a: None)


def test_send_nudge_missing_sender_raises():
    s = _settings()
    s.smtp_sender = ""
    with pytest.raises(RuntimeError):
        notify.send_nudge(s, 5, 2, password="pw", send_fn=lambda *a: None)


def test_send_nudge_missing_recipient_raises():
    s = _settings()
    s.smtp_recipient = ""
    with pytest.raises(RuntimeError):
        notify.send_nudge(s, 5, 2, password="pw", send_fn=lambda *a: None)
