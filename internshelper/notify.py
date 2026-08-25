"""The Top-target digest: new postings from explicitly prioritized companies.

"Here's what to apply to today" — not "come do review work". Fired by the collect
cycle only for rows never digested before (postings.notified_at IS NULL); the caller
stamps notified_at on success, so each posting is emailed exactly once. Reuses the
shared SMTP send helper from `mailer`.
"""

from __future__ import annotations

import html

from internshelper import mailer
from internshelper.config import Settings


def render_digest(rows) -> tuple[str, str]:
    """(subject, html) for the top-target digest. `rows` need title/company/url."""
    n = len(rows)
    subject = f"internsHELPer: {n} new Top-target posting{'' if n == 1 else 's'}"
    items = "".join(
        f'<li><a href="{html.escape(r["url"] or "", quote=True)}">'
        f"{html.escape(r['title'] or '')}</a>"
        f" — {html.escape(r['company'] or '')}</li>"
        for r in rows
    )
    body = (
        "<html><body>"
        f"<h2>{n} new posting{'' if n == 1 else 's'} from Top targets</h2>"
        f"<ul>{items}</ul>"
        "<p>Open the internsHELPer Board to browse the company (dismiss what's not you, "
        "drag to Applied when you've applied).</p>"
        "</body></html>"
    )
    return subject, body


def send_digest(
    settings: Settings,
    rows,
    password: str | None,
    send_fn=mailer.send,
) -> None:
    """Render + send the digest. Raises on missing password or SMTP failure."""
    if not password:
        raise RuntimeError("missing SMTP password (INTERNSHELPER_SMTP_PASSWORD)")
    if not settings.smtp_sender or not settings.smtp_recipient:
        raise RuntimeError(
            "missing SMTP sender/recipient — set INTERNSHELPER_SMTP_SENDER / "
            "INTERNSHELPER_SMTP_RECIPIENT (or [smtp] in settings.toml)"
        )
    subject, body = render_digest(rows)
    send_fn(settings, subject, body, password)
