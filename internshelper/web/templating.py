"""Jinja environment: template dir, layer-reusing filters, shared globals.

(Not named `filters.py` — internshelper.filters already exists.)
"""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from internshelper import display, store


def safe_url(url: object) -> str:
    """Scraped URLs are untrusted: allow http(s) only (a javascript: href inside the
    pywebview WKWebView is the nastiest payload a job board could hand us)."""
    u = str(url or "").strip()
    return u if u.startswith(("http://", "https://")) else "#"


def _build() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    env = templates.env
    env.filters["release"] = display.format_release
    env.filters["safe_url"] = safe_url
    env.globals["STATUS_OPTIONS"] = store.STATUS_OPTIONS
    env.globals["is_candidate"] = store.is_candidate
    return templates


templates = _build()
