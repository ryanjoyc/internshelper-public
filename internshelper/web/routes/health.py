"""Source health page + the launcher's liveness probe."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse

from internshelper import store
from internshelper.web.deps import get_conn, nav_context
from internshelper.web.templating import templates

router = APIRouter()


@router.get("/healthz")
def healthz() -> PlainTextResponse:
    # Probed by internshelper.app before the window opens; deliberately no DB touch —
    # a wedged database must not make the launcher think the server is down.
    return PlainTextResponse("ok")


@router.get("/health")
def health_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    runs = store.health(conn)
    baselines = store.source_baselines(conn)
    quiet = sorted(k for k, b in baselines.items() if b["quiet"])
    return templates.TemplateResponse(
        request,
        "health/index.html",
        {
            "nav": nav_context(conn),
            "active": "health",
            "runs": runs,
            "baselines": baselines,
            "quiet": quiet,
        },
    )
