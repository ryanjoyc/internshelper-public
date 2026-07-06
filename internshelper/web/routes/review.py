"""Triage: the pending-review queue (list + focus mode) and the root redirect."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from internshelper import review, store
from internshelper.web.deps import get_conn, nav_context
from internshelper.web.templating import templates

router = APIRouter()

PAGE_SIZE = 50


@router.get("/")
def root(conn: sqlite3.Connection = Depends(get_conn)) -> RedirectResponse:
    total, _ = store.pending_counts(conn)
    return RedirectResponse("/review" if total else "/board", status_code=303)


@router.get("/review")
def review_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    total, candidates = store.pending_counts(conn)
    rows = review.list_pending(conn, limit=PAGE_SIZE)
    return templates.TemplateResponse(
        request,
        "review/index.html",
        {
            "nav": nav_context(conn),
            "active": "review",
            "total": total,
            "candidates": candidates,
            "non_candidates": total - candidates,
            "rows": rows,
            "next_offset": len(rows) if total > len(rows) else None,
        },
    )
