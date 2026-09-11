"""Shared SMTP sender.

A single helper that delivers an HTML email via SMTP+STARTTLS. The review nudge
(`internshelper.notify`) is the only live caller; keeping the transport here means the
"how do we send mail" concern lives in one place, separate from "what do we send".
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from internshelper.config import Settings


def send(settings: Settings, subject: str, html: str, password: str) -> None:
    """Send an HTML email via SMTP+STARTTLS. Raises on any SMTP error."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_sender
    msg["To"] = settings.smtp_recipient
    msg.set_content("This message is best viewed as HTML.")
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
        server.starttls(context=ssl.create_default_context())
        server.login(settings.smtp_sender, password)
        server.send_message(msg)
