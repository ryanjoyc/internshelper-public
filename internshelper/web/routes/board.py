"""The kanban board (and its table lens) over confirmed matches."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request

from internshelper import store
from internshelper.web.deps import get_conn, nav_context
from internshelper.web.templating import templates

router = APIRouter()

LANES = ("Matched", "Applied", "Interviewing", "Offer", "Rejected")


def lane_of(status: str | None) -> str:
    # NULL / Untracked / Interested / legacy free-text all land in Matched;
    # Interested renders as a star on the card, not its own column.
    return status if status in LANES else "Matched"


@router.get("/board")
def board_page(
    request: Request, view: str = "board", conn: sqlite3.Connection = Depends(get_conn)
):
    rows = store.matches_with_status(conn)
    columns: dict[str, list] = {lane: [] for lane in LANES}
    for r in rows:
        columns[lane_of(r["status"])].append(r)
    return templates.TemplateResponse(
        request,
        "board/index.html",
        {
            "nav": nav_context(conn),
            "active": "board",
            "view": "table" if view == "table" else "board",
            "lanes": LANES,
            "columns": columns,
            "rows": rows,
        },
    )
