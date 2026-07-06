"""Sources page: configured boards + (from Phase 5) the add/remove wizard."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request

from internshelper import config
from internshelper.web.deps import get_conn, nav_context
from internshelper.web.templating import templates

router = APIRouter()


def load_entries(path) -> tuple[list, list, str | None]:
    """(entries, per-entry warnings, hard error) — malformed config is a banner, never a 500."""
    try:
        entries, errors = config.load_sources(path)
        return entries, errors, None
    except config.ConfigError as e:
        return [], [], str(e)


@router.get("/sources")
def sources_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    entries, errors, cfg_error = load_entries(request.app.state.sources_path)
    return templates.TemplateResponse(
        request,
        "sources/index.html",
        {
            "nav": nav_context(conn),
            "active": "sources",
            "entries": entries,
            "errors": errors,
            "cfg_error": cfg_error,
        },
    )
