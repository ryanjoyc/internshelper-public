"""Render + email the digest of new matching postings.

The `last_digest_at` watermark advances ONLY after a confirmed successful send (or a
nothing-due / cold-start cycle), so a failed SMTP send never silently drops a cycle's
alerts — the next cycle re-emits the same set.
"""

from __future__ import annotations

import smtplib
import sqlite3
from dataclasses import dataclass
from email.message import EmailMessage
from html import escape

from internshelper import db, store
from internshelper.config import Settings


@dataclass
class DigestResult:
    ok: bool
    sent: int
    cold_start: bool = False
    error: str | None = None


def render(rows: list[sqlite3.Row]) -> tuple[str, str]:
    """Build (subject, html) for the given postings (already newest-first)."""
    n = len(rows)
    subject = f"internsHELPer: {n} new role" + ("" if n == 1 else "s")
    blocks = []
    for r in rows:
        loc = escape(r["location"] or "—")
        blocks.append(
            f'<p><a href="{escape(r["url"], quote=True)}">{escape(r["title"])}</a>'
            f' — {escape(r["company"] or "")} <em>({loc})</em></p>'
        )
    html = (
        "<html><body>"
        f"<h2>{n} new matching role{'' if n == 1 else 's'}</h2>"
        + "".join(blocks)
        + "</body></html>"
    )
    return subject, html


def send(settings: Settings, subject: str, html: str, password: str) -> None:
    """Send the digest via SMTP+STARTTLS. Raises on any SMTP error."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_sender
    msg["To"] = settings.smtp_recipient
    msg.set_content("This digest is best viewed as HTML.")
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
        server.starttls()
        server.login(settings.smtp_sender, password)
        server.send_message(msg)


def run_digest(
    conn: sqlite3.Connection,
    settings: Settings,
    now: str,
    password: str | None,
    send_fn=send,
) -> DigestResult:
    last = db.get_meta(conn, "last_digest_at")

    # Cold start: seed the watermark, email nothing.
    if last is None:
        db.set_meta(conn, "last_digest_at", now)
        store.record_run(conn, "digest", ok=True, count=0, error=None, now=now)
        return DigestResult(ok=True, sent=0, cold_start=True)

    rows = store.select_for_digest(
        conn, last, settings.require_cs, settings.require_intern_or_newgrad
    )

    # Nothing due: advance the watermark, nothing to lose.
    if not rows:
        db.set_meta(conn, "last_digest_at", now)
        store.record_run(conn, "digest", ok=True, count=0, error=None, now=now)
        return DigestResult(ok=True, sent=0)

    if not password:
        msg = "missing SMTP password (INTERNSHELPER_SMTP_PASSWORD)"
        store.record_run(conn, "digest", ok=False, count=0, error=msg, now=now)
        return DigestResult(ok=False, sent=0, error=msg)

    subject, html = render(rows)
    try:
        send_fn(settings, subject, html, password)
    except Exception as e:  # send failed -> do NOT advance the watermark
        store.record_run(conn, "digest", ok=False, count=0, error=str(e), now=now)
        return DigestResult(ok=False, sent=0, error=str(e))

    db.set_meta(conn, "last_digest_at", now)
    store.record_run(conn, "digest", ok=True, count=len(rows), error=None, now=now)
    return DigestResult(ok=True, sent=len(rows))
