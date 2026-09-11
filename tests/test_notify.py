"""The Top-target digest email: rendering + SMTP-cred validation."""

import ssl

import pytest

from internshelper import mailer, notify
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


@pytest.mark.parametrize(
    "unsafe_url",
    ["javascript:alert(1)", "http://", "https://user:pass@example.test/", "http://["],
)
def test_render_digest_does_not_link_unsafe_urls(unsafe_url):
    rows = [{"title": "Unsafe", "company": "Example", "url": unsafe_url}]
    _subject, rendered = notify.render_digest(rows)
    assert unsafe_url not in rendered
    assert "<li>Unsafe — Example</li>" in rendered


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


def test_mailer_starttls_uses_default_certificate_verification(monkeypatch):
    context = object()
    calls = []

    class _SMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def starttls(self, *, context=None):
            calls.append(("starttls", context))

        def login(self, sender, password):
            calls.append(("login", sender, password))

        def send_message(self, message):
            calls.append(("send", message["To"]))

    monkeypatch.setattr(ssl, "create_default_context", lambda: context)
    monkeypatch.setattr(mailer.smtplib, "SMTP", _SMTP)

    mailer.send(_settings(), "subject", "<p>body</p>", "password")

    assert calls == [
        ("connect", "smtp.test", 587, mailer.SMTP_TIMEOUT),
        ("starttls", context),
        ("login", "me@test", "password"),
        ("send", "me@test"),
    ]
