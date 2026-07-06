"""The all-postings archive table: everything the collector has ever seen."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request

from internshelper import store
from internshelper.web.deps import get_conn, nav_context
from internshelper.web.templating import templates

router = APIRouter()

PER_PAGE = 100

# Filter vocabulary; "active" (the default) hides archived no-matches and closed listings.
STATES = ("active", "pending", "matched", "archived", "closed", "all")


def _keep(r: sqlite3.Row, state: str, source: str) -> bool:
    if source and r["source_key"] != source:
        return False
    if state == "pending":
        return bool(r["is_active"]) and r["review_status"] == "pending"
    if state == "matched":
        return r["verdict"] == "match"
    if state == "archived":
        return r["verdict"] == "no_match"
    if state == "closed":
        return not r["is_active"]
    if state == "all":
        return True
    return bool(r["is_active"]) and r["verdict"] != "no_match"  # "active" default


@router.get("/postings")
def postings_page(
    request: Request,
    q: str = "",
    state: str = "active",
    source: str = "",
    page: int = 1,
    conn: sqlite3.Connection = Depends(get_conn),
):
    if state not in STATES:
        state = "active"
    # The archive is small (hundreds of rows, local SQLite): fetch the searched set once
    # and filter/paginate in Python rather than growing store.feed a verdict-filter API.
    rows = store.feed(
        conn,
        require_cs=False,
        require_intern_or_newgrad=False,
        include_all=True,
        include_closed=True,
        search=q,
    )
    sources_seen = sorted({r["source_key"] for r in rows})
    filtered = [r for r in rows if _keep(r, state, source)]
    pages = max(1, -(-len(filtered) // PER_PAGE))
    page = min(max(page, 1), pages)
    window = filtered[(page - 1) * PER_PAGE : page * PER_PAGE]
    return templates.TemplateResponse(
        request,
        "postings/index.html",
        {
            "nav": nav_context(conn),
            "active": "postings",
            "rows": window,
            "total": len(filtered),
            "page": page,
            "pages": pages,
            "q": q,
            "state": state,
            "states": STATES,
            "source": source,
            "sources_seen": sources_seen,
        },
    )
