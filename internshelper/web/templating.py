"""Jinja environment: template dir, layer-reusing filters, shared globals.

(Not named `filters.py` — internshelper.filters already exists.)
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.templating import Jinja2Templates

from internshelper import display, store

_DOM_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def dom_id(value: object) -> str:
    """Posting ids contain ':' — unusable in CSS id selectors. DOM ids use this form;
    the real id always rides in a data-id attribute / form field."""
    return _DOM_UNSAFE.sub("-", str(value))


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
    env.filters["dom_id"] = dom_id
    env.globals["STATUS_OPTIONS"] = store.STATUS_OPTIONS
    env.globals["is_candidate"] = store.is_candidate
    return templates


templates = _build()
