"""Triage: the pending-review queue (list + focus mode) and the root redirect.

Every verdict path (single + bulk) ends with review.finish() — the email-nudge
contract. Undo (reset_verdict / undo_bulk_clear) never touches the flag.
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from internshelper import clock, ranking, review, store
from internshelper.web.deps import get_conn, nav_context
from internshelper.web.routes.sources import load_entries
from internshelper.web.templating import templates

router = APIRouter()

VIEWS = ("list", "pane", "focus")
VIEW_COOKIE = "review_view"


def _resolve_view(request: Request, mode: str | None) -> str:
    """Explicit ?mode= wins (and gets remembered); else the cookie; else tiers."""
    if mode is not None:
        return mode if mode in VIEWS else "list"
    cookie = request.cookies.get(VIEW_COOKIE, "")
    return cookie if cookie in VIEWS else "list"


def _rescore(conn: sqlite3.Connection) -> None:
    """Retrain the learned ranker after a verdict mutation — every verdict is a new
    label. Best-effort: a ranking bug must never block triage."""
    try:
        ranking.rescore_inbox(conn, now=clock.now_iso())
    except Exception:
        pass


def _filters(q: str = "", source: str = "", sort: str = "rank") -> dict:
    """Normalized filter state for the queue; bad sorts coerce to the default."""
    return {
        "q": q.strip(),
        "source": source,
        "sort": sort if sort in review.SORTS else "rank",
    }


def _filter_ctx(conn: sqlite3.Connection, filters: dict) -> dict:
    """Filter-related template context shared by every _list_region renderer."""
    filtered = bool(filters["q"] or filters["source"])
    return {
        "filters": filters,
        "filtered": filtered,
        "shown": review.count_pending_filtered(conn, q=filters["q"], source=filters["source"])
        if filtered
        else None,
        "source_options": review.pending_source_keys(conn),
    }


def _hygiene_counts(request: Request, conn: sqlite3.Connection) -> dict:
    """Counts for the queue-hygiene buttons. A broken sources.yaml means no leak
    detection this render (count 0) — never a 500."""
    entries, _, cfg_error = load_entries(request.app.state.sources_path)
    leaks = review.find_guard_leaks(conn, entries) if not cfg_error else []
    return {
        "leak_count": len(leaks),
        "closed_count": len(review.find_closed_inbox(conn)),
    }


def _counts_ctx(conn: sqlite3.Connection) -> dict:
    total, candidates = store.pending_counts(conn)
    return {
        "nav": nav_context(conn),
        "total": total,
        "candidates": candidates,
        "non_candidates": total - candidates,
        # Global (unfiltered) tier sizes — the header's "N need your call" sentence.
        "tier_counts": review.tier_counts(conn, high=ranking.TIER_HIGH, low=ranking.TIER_LOW),
    }


def _pane_ctx(conn: sqlite3.Connection, filters: dict, offset: int = 0) -> dict:
    """The split-pane view: the filtered flat queue + the detail card at `offset`.

    Unlike focus mode (which walks the GLOBAL queue), pane offsets index the
    filtered, sorted list — the left list and the detail always agree.
    """
    ctx = _filter_ctx(conn, filters)
    rows = review.list_pending(conn, q=filters["q"], source=filters["source"],
                               sort=filters["sort"])
    pane_total = len(rows)
    offset = min(max(offset, 0), max(pane_total - 1, 0))
    card = rows[offset] if rows else None
    payload = review.payload_summary(card["payload_path"]) if card else None
    ctx.update(
        {
            "view": "pane",
            "pane_rows": rows,
            "pane_total": pane_total,
            "offset": offset,
            "card": card,
            "payload": payload,
            "raw_json": json.dumps(payload["raw"], indent=2, default=str)
            if payload and payload["raw"] is not None
            else None,
        }
    )
    return ctx


def _list_ctx(conn: sqlite3.Connection, filters: dict) -> dict:
    """The tiered list region: the WHOLE filtered queue, partitioned by tier.

    No paging — collapsed tiers hide their rows, and the open tier is the short one;
    a local single-user queue renders comfortably in full. `global_offset` is each
    row's index in this list order, the Enter→focus-mode jump target.
    """
    rows = review.list_pending(conn, q=filters["q"], source=filters["source"],
                               sort=filters["sort"])
    for i, row in enumerate(rows):
        row["global_offset"] = i
    ctx = _filter_ctx(conn, filters)
    ctx["tiers"] = review.partition_tiers(rows, high=ranking.TIER_HIGH, low=ranking.TIER_LOW)
    ctx["any_rows"] = bool(rows)
    return ctx


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
    mode: str | None = None,
    offset: int = 0,
    q: str = "",
    source: str = "",
    sort: str = "rank",
    conn: sqlite3.Connection = Depends(get_conn),
):
    view = _resolve_view(request, mode)
    if view == "focus":
        # Focus mode deliberately ignores filters: it walks global pending offsets.
        ctx = _focus_ctx(conn, offset)
        ctx.update({"active": "review", "view": view})
        resp = templates.TemplateResponse(request, "review/focus.html", ctx)
    else:
        ctx = _counts_ctx(conn)
        ctx.update(_hygiene_counts(request, conn))
        f = _filters(q, source, sort)
        ctx.update(_pane_ctx(conn, f, offset) if view == "pane" else _list_ctx(conn, f))
        ctx.update({"active": "review", "view": view})
        resp = templates.TemplateResponse(request, "review/index.html", ctx)
    resp.set_cookie(VIEW_COOKIE, view, max_age=31536000, samesite="lax")
    return resp


@router.get("/review/pane-card")
def pane_card(
    request: Request,
    offset: int = 0,
    q: str = "",
    source: str = "",
    sort: str = "rank",
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Detail-only swap when a pane-list item is clicked."""
    ctx = _pane_ctx(conn, _filters(q, source, sort), offset)
    return templates.TemplateResponse(request, "review/_pane_detail.html", ctx)


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
    q: str = Form(""),
    source: str = Form(""),
    sort: str = Form("rank"),
    conn: sqlite3.Connection = Depends(get_conn),
):
    if verdict not in review.VERDICTS:
        raise HTTPException(status_code=400, detail=f"verdict must be one of {review.VERDICTS}")
    posting = _posting(conn, posting_id)
    review.set_verdict(conn, posting_id, verdict, reason, now=clock.now_iso())
    review.finish(conn)  # re-arms the email nudge the moment the queue empties
    _rescore(conn)

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
    if mode == "pane":
        # Same offset in the now-shorter list = the next posting (clamped at the end).
        ctx = _counts_ctx(conn)
        ctx.update(_pane_ctx(conn, _filters(q, source, sort), offset))
        ctx.update({"toast": toast, "oob": True})
        return templates.TemplateResponse(request, "review/_pane_region.html", ctx)
    ctx = _counts_ctx(conn)
    f = _filters(q, source, sort)
    ctx.update(_filter_ctx(conn, f))
    # Tier-head counters show the FILTERED queue, so their OOB refresh must too.
    ctx["shown_tier_counts"] = review.tier_counts(
        conn, high=ranking.TIER_HIGH, low=ranking.TIER_LOW, q=f["q"], source=f["source"]
    )
    ctx["toast"] = toast
    return templates.TemplateResponse(request, "review/_verdict_response.html", ctx)


@router.post("/review/undo")
def post_undo(
    request: Request,
    posting_id: str = Form(...),
    mode: str = Form("list"),
    q: str = Form(""),
    source: str = Form(""),
    sort: str = Form("rank"),
    conn: sqlite3.Connection = Depends(get_conn),
):
    _posting(conn, posting_id)
    review.reset_verdict(conn, posting_id)
    _rescore(conn)
    if mode == "focus":
        # Land on the restored posting so the user sees what came back.
        pending_ids = [r["posting_id"] for r in review.list_pending(conn)]
        offset = pending_ids.index(posting_id) if posting_id in pending_ids else 0
        ctx = _focus_ctx(conn, offset)
        ctx["oob"] = True
        return templates.TemplateResponse(request, "review/_focus_card.html", ctx)
    f = _filters(q, source, sort)
    if mode == "pane":
        # Land on the restored posting (its index in the FILTERED order).
        rows = review.list_pending(conn, q=f["q"], source=f["source"], sort=f["sort"])
        ids = [r["posting_id"] for r in rows]
        offset = ids.index(posting_id) if posting_id in ids else 0
        ctx = _counts_ctx(conn)
        ctx.update(_pane_ctx(conn, f, offset))
        ctx["oob"] = True
        return templates.TemplateResponse(request, "review/_pane_region.html", ctx)
    ctx = _counts_ctx(conn)
    ctx.update(_list_ctx(conn, f))
    ctx["oob"] = True
    return templates.TemplateResponse(request, "review/_list_region.html", ctx)


def _bulk_response(request, conn, *, filters: dict, view: str = "list",
                   cleared: int | None = None, now: str = "", reason: str = "",
                   noun: str = "", verb: str = "Cleared"):
    """Re-render the queue region (tiers or pane, per the caller's view) after a bulk
    action.

    The hygiene clears are GLOBAL in scope (filters only shape the redisplay);
    the tier accept/dismiss actions are filter-scoped by their callers.
    """
    ctx = _counts_ctx(conn)
    if view == "pane":
        ctx.update(_pane_ctx(conn, filters))
    else:
        ctx.update(_list_ctx(conn, filters))
    ctx["oob"] = True
    if cleared is not None:
        ctx["bulk_toast"] = {"cleared": cleared, "reviewed_at": now,
                             "reason": reason, "noun": noun, "verb": verb}
    template = "review/_pane_region.html" if view == "pane" else "review/_list_region.html"
    return templates.TemplateResponse(request, template, ctx)


@router.post("/review/bulk-clear")
def post_bulk_clear(
    request: Request,
    q: str = Form(""),
    source: str = Form(""),
    sort: str = Form("rank"),
    mode: str = Form("list"),
    conn: sqlite3.Connection = Depends(get_conn),
):
    now = clock.now_iso()
    cleared = review.clear_non_candidate_pending(conn, now=now)
    review.finish(conn)
    _rescore(conn)
    return _bulk_response(request, conn, filters=_filters(q, source, sort), view=mode,
                          cleared=cleared, now=now, reason=review.BULK_CLEAR_REASON,
                          noun="non-candidate")


@router.post("/review/bulk-clear-leaks")
def post_bulk_clear_leaks(
    request: Request,
    q: str = Form(""),
    source: str = Form(""),
    sort: str = Form("rank"),
    mode: str = Form("list"),
    conn: sqlite3.Connection = Depends(get_conn),
):
    entries, _, cfg_error = load_entries(request.app.state.sources_path)
    now = clock.now_iso()
    cleared = 0 if cfg_error else review.clear_guard_leaks(conn, entries, now=now)
    review.finish(conn)
    _rescore(conn)
    return _bulk_response(request, conn, filters=_filters(q, source, sort), view=mode,
                          cleared=cleared, now=now, reason=review.GUARD_LEAK_REASON,
                          noun="guard leak")


@router.post("/review/bulk-clear-closed")
def post_bulk_clear_closed(
    request: Request,
    q: str = Form(""),
    source: str = Form(""),
    sort: str = Form("rank"),
    mode: str = Form("list"),
    conn: sqlite3.Connection = Depends(get_conn),
):
    now = clock.now_iso()
    cleared = review.clear_closed_inbox(conn, now=now)
    review.finish(conn)
    _rescore(conn)
    return _bulk_response(request, conn, filters=_filters(q, source, sort), view=mode,
                          cleared=cleared, now=now, reason=review.CLOSED_REASON,
                          noun="closed posting")


@router.post("/review/bulk-accept")
def post_bulk_accept(
    request: Request,
    q: str = Form(""),
    source: str = Form(""),
    sort: str = Form("rank"),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Match everything in the near-certain tier. Filter-SCOPED, unlike the hygiene
    clears: the button acts on exactly the tier the user is looking at."""
    f = _filters(q, source, sort)
    now = clock.now_iso()
    accepted = review.accept_high_tier(conn, now, threshold=ranking.TIER_HIGH,
                                       q=f["q"], source=f["source"])
    review.finish(conn)
    _rescore(conn)
    return _bulk_response(request, conn, filters=f, cleared=accepted, now=now,
                          reason=review.TIER_ACCEPT_REASON, noun="sure thing",
                          verb="Accepted")


@router.post("/review/bulk-dismiss")
def post_bulk_dismiss(
    request: Request,
    q: str = Form(""),
    source: str = Form(""),
    sort: str = Form("rank"),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """No-match everything the model scored below the low tier. Filter-scoped."""
    f = _filters(q, source, sort)
    now = clock.now_iso()
    dismissed = review.dismiss_low_tier(conn, now, threshold=ranking.TIER_LOW,
                                        q=f["q"], source=f["source"])
    review.finish(conn)
    _rescore(conn)
    return _bulk_response(request, conn, filters=f, cleared=dismissed, now=now,
                          reason=review.TIER_DISMISS_REASON, noun="long shot",
                          verb="Dismissed")


@router.post("/review/bulk-undo")
def post_bulk_undo(
    request: Request,
    reviewed_at: str = Form(...),
    reason: str = Form(...),
    q: str = Form(""),
    source: str = Form(""),
    sort: str = Form("rank"),
    mode: str = Form("list"),
    conn: sqlite3.Connection = Depends(get_conn),
):
    review.undo_bulk_clear(conn, reviewed_at=reviewed_at, reason=reason)
    _rescore(conn)
    return _bulk_response(request, conn, filters=_filters(q, source, sort), view=mode)


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
