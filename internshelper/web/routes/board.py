"""The kanban board (and its table lens) over confirmed matches.

Drag moves are status-only writes (set_application_status — never the 3-column
upsert, which would clobber notes); the drawer's Save is the full upsert.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, HTTPException, Request

from internshelper import clock, review, store
from internshelper.web.deps import get_conn, nav_context
from internshelper.web.templating import templates

router = APIRouter()

LANES = ("Matched", "Applied", "Interviewing", "Offer", "Rejected")


def lane_of(status: str | None) -> str:
    # NULL / Untracked / Interested / legacy free-text all land in Matched;
    # Interested renders as a star on the card, not its own column.
    return status if status in LANES else "Matched"


def _board_ctx(conn: sqlite3.Connection, view: str) -> dict:
    rows = store.matches_with_status(conn)
    columns: dict[str, list] = {lane: [] for lane in LANES}
    for r in rows:
        columns[lane_of(r["status"])].append(r)
    return {
        "nav": nav_context(conn),
        "view": "table" if view == "table" else "board",
        "lanes": LANES,
        "columns": columns,
        "rows": rows,
    }


def _match_row(conn: sqlite3.Connection, posting_id: str) -> sqlite3.Row:
    for r in store.matches_with_status(conn):
        if r["posting_id"] == posting_id:
            return r
    raise HTTPException(status_code=404, detail=f"no confirmed match {posting_id!r}")


@router.get("/board")
def board_page(
    request: Request, view: str = "board", conn: sqlite3.Connection = Depends(get_conn)
):
    ctx = _board_ctx(conn, view)
    ctx["active"] = "board"
    return templates.TemplateResponse(request, "board/index.html", ctx)


@router.get("/board/card/{posting_id}")
def drawer(
    request: Request,
    posting_id: str,
    view: str = "board",
    conn: sqlite3.Connection = Depends(get_conn),
):
    r = _match_row(conn, posting_id)
    payload = review.payload_summary(r["payload_path"])
    response = templates.TemplateResponse(
        request,
        "drawer/_posting.html",
        {"r": r, "view": view, "payload": payload},
    )
    response.headers["HX-Trigger"] = "drawer-open"
    return response


@router.post("/board/card/{posting_id}")
def drawer_save(
    request: Request,
    posting_id: str,
    status: str = Form(...),
    applied_date: str = Form(""),
    notes: str = Form(""),
    view: str = Form("board"),
    conn: sqlite3.Connection = Depends(get_conn),
):
    _match_row(conn, posting_id)
    try:
        store.set_application(
            conn, posting_id, status=status, notes=notes, applied_date=applied_date or None
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    r = _match_row(conn, posting_id)
    payload = review.payload_summary(r["payload_path"])
    ctx = _board_ctx(conn, view)
    ctx.update({"r": r, "view": view, "payload": payload, "oob_board": True, "saved": True})
    return templates.TemplateResponse(request, "drawer/_posting.html", ctx)


@router.post("/board/move")
def move(
    request: Request,
    posting_id: str = Form(...),
    status: str = Form(...),
    region: str = Form(""),
    conn: sqlite3.Connection = Depends(get_conn),
):
    prev = store.get_application(conn, posting_id)
    prev_status = (prev["status"] if prev else None) or "Untracked"
    _match_row(conn, posting_id)
    try:
        store.set_application_status(
            conn,
            posting_id,
            status,
            applied_date_if_empty=clock.now_iso()[:10] if status == "Applied" else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if region:  # undo path: the DOM wasn't pre-moved by a drag, re-render the whole board
        ctx = _board_ctx(conn, "board")
        ctx["oob_nav"] = True
        return templates.TemplateResponse(request, "board/_region.html", ctx)
    r = _match_row(conn, posting_id)
    ctx = {
        "nav": nav_context(conn),
        "r": r,
        "view": "board",
        "columns_counts": _board_ctx(conn, "board")["columns"],
        "lanes": LANES,
        "move_toast": {"posting_id": posting_id, "to": status, "prev": prev_status},
    }
    return templates.TemplateResponse(request, "board/_move_response.html", ctx)


@router.post("/board/unmatch")
def unmatch(
    request: Request,
    posting_id: str = Form(...),
    view: str = Form("board"),
    conn: sqlite3.Connection = Depends(get_conn),
):
    _match_row(conn, posting_id)
    review.reset_verdict(conn, posting_id)  # back to the pending queue; not a verdict, no finish()
    ctx = _board_ctx(conn, view)
    ctx["oob_nav"] = True
    response = templates.TemplateResponse(request, "board/_region.html", ctx)
    response.headers["HX-Trigger"] = "drawer-close"
    return response
