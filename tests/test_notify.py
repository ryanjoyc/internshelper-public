"""The Top-target digest email: rendering + SMTP-cred validation."""

import pytest

from internshelper import notify
from internshelper.config import Settings


def _settings():
    return Settings(
        smtp_host="smtp.test", smtp_port=587, smtp_sender="me@test", smtp_recipient="me@test",
        require_cs=True, require_intern_or_newgrad=True,
        keywords={"internship": ["intern"], "newgrad": ["new grad"], "cs": ["software"]},
    )


def _rows():
    return [
        {"title": "SWE Intern", "company": "Google", "url": "https://x/1"},
        {"title": "Quant <Dev> & Co", "company": "Jane Street", "url": "https://x/2?a=1&b=2"},
    ]


def test_render_digest_lists_titles_companies_links():
    subject, html = notify.render_digest(_rows())
    assert subject == "internsHELPer: 2 new Top-target postings"
    assert '<a href="https://x/1">SWE Intern</a>' in html
    assert "Google" in html and "Jane Street" in html
    # HTML in scraped titles/urls is escaped, never rendered
    assert "Quant &lt;Dev&gt; &amp; Co" in html
    assert "https://x/2?a=1&amp;b=2" in html


def test_render_digest_singular_subject():
    subject, _ = notify.render_digest(_rows()[:1])
    assert subject == "internsHELPer: 1 new Top-target posting"


def test_send_digest_missing_password_raises():
    with pytest.raises(RuntimeError):
        notify.send_digest(_settings(), _rows(), password=None, send_fn=lambda *a: None)


def test_send_digest_missing_sender_raises():
    s = _settings()
    s.smtp_sender = ""
    with pytest.raises(RuntimeError):
        notify.send_digest(s, _rows(), password="pw", send_fn=lambda *a: None)


def test_send_digest_missing_recipient_raises():
    s = _settings()
    s.smtp_recipient = ""
    with pytest.raises(RuntimeError):
        notify.send_digest(s, _rows(), password="pw", send_fn=lambda *a: None)
