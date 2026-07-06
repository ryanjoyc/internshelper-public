"""Triage: the pending-review queue (list + focus mode) and the root redirect.

Every verdict path (single + bulk) ends with review.finish() — the email-nudge
contract. Undo (reset_verdict / undo_bulk_clear) never touches the flag.
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from internshelper import clock, review, store
from internshelper.web.deps import get_conn, nav_context
from internshelper.web.templating import templates

router = APIRouter()

PAGE_SIZE = 50


def _counts_ctx(conn: sqlite3.Connection) -> dict:
    total, candidates = store.pending_counts(conn)
    return {
        "nav": nav_context(conn),
        "total": total,
        "candidates": candidates,
        "non_candidates": total - candidates,
    }


def _posting(conn: sqlite3.Connection, posting_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT posting_id, title, company, payload_path FROM postings WHERE posting_id = ?",
        (posting_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown posting {posting_id!r}")
    return row


def _focus_ctx(conn: sqlite3.Connection, offset: int) -> dict:
    """The focus card at `offset` in pending order (clamped), plus its payload summary."""
    ctx = _counts_ctx(conn)
    total = ctx["total"]
    offset = min(max(offset, 0), max(total - 1, 0))
    rows = review.list_pending(conn, limit=1, offset=offset)
    card = rows[0] if rows else None
    payload = review.payload_summary(card["payload_path"]) if card else None
    ctx.update(
        {
            "card": card,
            "offset": offset,
            "payload": payload,
            "raw_json": json.dumps(payload["raw"], indent=2, default=str)
            if payload and payload["raw"] is not None
            else None,
        }
    )
    return ctx


@router.get("/")
def root(conn: sqlite3.Connection = Depends(get_conn)) -> RedirectResponse:
    total, _ = store.pending_counts(conn)
    return RedirectResponse("/review" if total else "/board", status_code=303)


@router.get("/review")
def review_page(
    request: Request,
    mode: str = "list",
    offset: int = 0,
    conn: sqlite3.Connection = Depends(get_conn),
):
    if mode == "focus":
        ctx = _focus_ctx(conn, offset)
        ctx["active"] = "review"
        return templates.TemplateResponse(request, "review/focus.html", ctx)
    ctx = _counts_ctx(conn)
    rows = review.list_pending(conn, limit=PAGE_SIZE)
    ctx.update(
        {
            "active": "review",
            "rows": rows,
            "offset": 0,
            "more": len(rows) == PAGE_SIZE and ctx["total"] > PAGE_SIZE,
        }
    )
    return templates.TemplateResponse(request, "review/index.html", ctx)


@router.get("/review/queue")
def queue_partial(
    request: Request, offset: int = 0, conn: sqlite3.Connection = Depends(get_conn)
):
    """Next slice for the infinite scroll; `offset` is the caller's rendered-row count."""
    ctx = _counts_ctx(conn)
    rows = review.list_pending(conn, limit=PAGE_SIZE, offset=offset)
    ctx.update({"rows": rows, "offset": offset, "more": len(rows) == PAGE_SIZE})
    return templates.TemplateResponse(request, "review/_rows.html", ctx)


@router.get("/review/focus-card")
def focus_card(
    request: Request, offset: int = 0, conn: sqlite3.Connection = Depends(get_conn)
):
    return templates.TemplateResponse(request, "review/_focus_card.html", _focus_ctx(conn, offset))


@router.post("/review/verdict")
def post_verdict(
    request: Request,
    posting_id: str = Form(...),
    verdict: str = Form(...),
    reason: str = Form(""),
    mode: str = Form("list"),
    offset: int = Form(0),
    conn: sqlite3.Connection = Depends(get_conn),
):
    if verdict not in review.VERDICTS:
        raise HTTPException(status_code=400, detail=f"verdict must be one of {review.VERDICTS}")
    posting = _posting(conn, posting_id)
    review.set_verdict(conn, posting_id, verdict, reason, now=clock.now_iso())
    review.finish(conn)  # re-arms the email nudge the moment the queue empties

    toast = {
        "verdict": verdict,
        "posting_id": posting_id,
        "title": posting["title"],
        "company": posting["company"],
        "mode": mode,
    }
    if mode == "focus":
        ctx = _focus_ctx(conn, offset)
        ctx.update({"toast": toast, "oob": True})
        return templates.TemplateResponse(request, "review/_focus_card.html", ctx)
    ctx = _counts_ctx(conn)
    ctx["toast"] = toast
    return templates.TemplateResponse(request, "review/_verdict_response.html", ctx)


@router.post("/review/undo")
def post_undo(
    request: Request,
    posting_id: str = Form(...),
    mode: str = Form("list"),
    conn: sqlite3.Connection = Depends(get_conn),
):
    _posting(conn, posting_id)
    review.reset_verdict(conn, posting_id)
    if mode == "focus":
        # Land on the restored posting so the user sees what came back.
        pending_ids = [r["posting_id"] for r in review.list_pending(conn)]
        offset = pending_ids.index(posting_id) if posting_id in pending_ids else 0
        ctx = _focus_ctx(conn, offset)
        ctx["oob"] = True
        return templates.TemplateResponse(request, "review/_focus_card.html", ctx)
    ctx = _counts_ctx(conn)
    rows = review.list_pending(conn, limit=PAGE_SIZE)
    ctx.update(
        {"rows": rows, "offset": 0, "more": len(rows) == PAGE_SIZE, "oob": True}
    )
    return templates.TemplateResponse(request, "review/_list_region.html", ctx)


@router.post("/review/bulk-clear")
def post_bulk_clear(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    now = clock.now_iso()
    cleared = review.clear_non_candidate_pending(conn, now=now)
    review.finish(conn)
    ctx = _counts_ctx(conn)
    rows = review.list_pending(conn, limit=PAGE_SIZE)
    ctx.update(
        {
            "rows": rows,
            "offset": 0,
            "more": len(rows) == PAGE_SIZE,
            "oob": True,
            "bulk_toast": {"cleared": cleared, "reviewed_at": now,
                           "reason": review.BULK_CLEAR_REASON},
        }
    )
    return templates.TemplateResponse(request, "review/_list_region.html", ctx)


@router.post("/review/bulk-undo")
def post_bulk_undo(
    request: Request,
    reviewed_at: str = Form(...),
    reason: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    review.undo_bulk_clear(conn, reviewed_at=reviewed_at, reason=reason)
    ctx = _counts_ctx(conn)
    rows = review.list_pending(conn, limit=PAGE_SIZE)
    ctx.update(
        {"rows": rows, "offset": 0, "more": len(rows) == PAGE_SIZE, "oob": True}
    )
    return templates.TemplateResponse(request, "review/_list_region.html", ctx)


@router.get("/review/peek/{posting_id}")
def peek(request: Request, posting_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    posting = _posting(conn, posting_id)
    summary = review.payload_summary(posting["payload_path"])
    return templates.TemplateResponse(
        request,
        "review/_peek.html",
        {
            "summary": summary,
            "raw_json": json.dumps(summary["raw"], indent=2, default=str)
            if summary["raw"] is not None
            else None,
        },
    )
