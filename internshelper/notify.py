"""The review nudge: a one-line email telling the user a batch is ready to classify.

Replaces v1's hourly matches-digest. Reuses the shared SMTP send helper from `mailer`.
"""

from __future__ import annotations

from internshelper import mailer
from internshelper.config import Settings


def render_nudge(pending_count: int, candidate_count: int) -> tuple[str, str]:
    other = max(pending_count - candidate_count, 0)
    subject = f"internsHELPer: {pending_count} postings ready to review"
    html = (
        "<html><body>"
        f"<h2>{pending_count} new postings pending review</h2>"
        f"<p>{candidate_count} keyword-candidate, {other} other.</p>"
        "<p>Open the internsHELPer repo in Claude Code and run "
        "<code>/review-internships</code> to classify them.</p>"
        "</body></html>"
    )
    return subject, html


def send_nudge(
    settings: Settings,
    pending_count: int,
    candidate_count: int,
    password: str | None,
    send_fn=mailer.send,
) -> None:
    """Render + send the nudge. Raises on missing password or SMTP failure."""
    if not password:
        raise RuntimeError("missing SMTP password (INTERNSHELPER_SMTP_PASSWORD)")
    if not settings.smtp_sender or not settings.smtp_recipient:
        raise RuntimeError(
            "missing SMTP sender/recipient — set INTERNSHELPER_SMTP_SENDER / "
            "INTERNSHELPER_SMTP_RECIPIENT (or [smtp] in settings.toml)"
        )
    subject, html = render_nudge(pending_count, candidate_count)
    send_fn(settings, subject, html, password)
