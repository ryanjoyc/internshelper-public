"""The Board: the whole product on one page.

There is no approval gate — every collected posting lands in the first column (the
Inbox, grouped by company-priority sections), and the pipeline lanes to its right
track what the user acted on. Dragging a card Inbox → Applied IS the apply action;
Dismiss hides it (undoable). Every mutation ends with a best-effort rescore+retier —
each action is a new ranking label.

Drag moves are status-only writes (set_application_status — never the 3-column
upsert, which would clobber notes); the drawer's Save is the full upsert.
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from internshelper import clock, companygroups, config, db, dedup, review, store, tiers
from internshelper.web.deps import get_conn, nav_context
from internshelper.web.templating import templates

router = APIRouter()

PIPELINE_LANES = store.PIPELINE_STATUSES  # ("Applied", "Interviewing", "Offer", "Rejected")

# Company sections inside the Inbox column, in fixed display order.
TIER_UI = [
    {"key": key, "label": tiers.TIER_LABELS[key], "hint": tiers.TIER_HINTS[key],
     "open": key != "unclassified"}
    for key in tiers.TIERS
]


HIDDEN_LANES_KEY = "board_hidden_lanes"


def _hidden_lanes(conn: sqlite3.Connection) -> set[str]:
    """Lanes the user collapsed to a strip — display state only, never data."""
    try:
        stored = json.loads(db.get_meta(conn, HIDDEN_LANES_KEY) or "[]")
    except ValueError:
        return set()
    return {lane for lane in stored if lane in PIPELINE_LANES} if isinstance(stored, list) else set()


def lane_of(status: str | None) -> str:
    # NULL / Untracked / Interested / legacy free-text all mean "still in the Inbox";
    # Interested renders as a star on the card, not its own column.
    return status if status in PIPELINE_LANES else "Inbox"


def _refresh(conn: sqlite3.Connection) -> None:
    """Rescore + retier after a mutation — every action is a new label. Best-effort:
    a ranking bug must never block the action itself."""
    review.refresh_ranking(conn)


def _self_heal_tiers(conn: sqlite3.Connection, groups_path: str) -> None:
    """Migrate old posting-tier values on first load after this redesign."""
    marks = ",".join("?" for _ in tiers.TIERS)
    stale = conn.execute(
        f"SELECT 1 FROM postings WHERE (tier IS NULL OR tier NOT IN ({marks})) "
        f"AND {store.INBOX_SQL} LIMIT 1",
        tiers.TIERS,
    ).fetchone()
    if stale:
        tiers.retier_inbox(conn, tiers.load_tier_map(groups_path))


def _group_by_company(rows: list, tier_map) -> list[dict]:
    """Group newest-first postings by canonical configured company identity."""
    def value(row, key):
        return row.get(key) if isinstance(row, dict) else row[key]

    groups: dict[str, dict] = {}
    for r in rows:
        raw = (r["company"] or "").strip()
        group, entry = companygroups.classify(raw, tier_map)
        display = entry.name if entry else (raw or "Unknown")
        slug = tiers.normalize_company(display) or "—unknown"
        g = groups.get(slug)
        if g is None:
            groups[slug] = {
                "company": display, "slug": slug, "group": group,
                "reason": entry.reason if entry else "", "rows": [r],
                "newest": value(r, "posted_at") or value(r, "first_seen"),
            }
        else:
            g["rows"].append(r)
    return list(groups.values())


def _inbox_date_key(r) -> tuple:
    """Newest-first sort key: actual post date if known, else when we first saw it."""
    return (r["posted_at"] or r["first_seen"] or "", r["first_seen"] or "", r["posting_id"])


def _board_ctx(conn: sqlite3.Connection, view: str, groups_path: str) -> dict:
    rows = store.inbox_with_status(conn)
    columns: dict[str, list] = {lane: [] for lane in PIPELINE_LANES}
    inbox_rows: list = []
    for r in rows:
        lane = lane_of(r["status"])
        if lane == "Inbox":
            inbox_rows.append(r)
        else:
            columns[lane].append(r)
    # Company group is authoritative. Rank score cannot move or hide a role.
    inbox_rows.sort(key=_inbox_date_key, reverse=True)
    tier_map = tiers.load_tier_map(groups_path)
    inbox_groups = _group_by_company(inbox_rows, tier_map)
    inbox_tiers = []
    for item in TIER_UI:
        groups = [g for g in inbox_groups if g["group"] == item["key"]]
        inbox_tiers.append({
            **item, "groups": groups, "company_count": len(groups),
            "posting_count": sum(len(g["rows"]) for g in groups),
        })
    return {
        "nav": nav_context(conn),
        "view": "table" if view == "table" else "board",
        "pipeline_lanes": PIPELINE_LANES,
        "columns": columns,
        "inbox_groups": inbox_groups,
        "inbox_tiers": inbox_tiers,
        "company_group_options": TIER_UI,
        "inbox_total": sum(len(g["rows"]) for g in inbox_groups),
        "hidden_lanes": _hidden_lanes(conn),
        "flagged_count": store.flagged_count(conn),
        "dup_count": review.count_possible_duplicates(conn),
        "rows": rows,
        "hygiene": _hygiene_counts(conn),
    }


def _hygiene_counts(conn: sqlite3.Connection) -> dict:
    """Counts for the one-click cleanup buttons (guard leaks + closed-but-listed)."""
    try:
        entries, _ = config.load_sources(
            config.default_path("INTERNSHELPER_SOURCES", "config/sources.yaml")
        )
        leaks = len(review.find_guard_leaks(conn, entries))
    except Exception:
        leaks = 0
    return {"leaks": leaks, "closed": len(review.find_closed_inbox(conn))}


def _board_row(conn: sqlite3.Connection, posting_id: str) -> sqlite3.Row:
    for r in store.inbox_with_status(conn):
        if r["posting_id"] == posting_id:
            return r
    raise HTTPException(status_code=404, detail=f"no board posting {posting_id!r}")


def _region(request: Request, conn: sqlite3.Connection, *, view: str = "board",
            toast: dict | None = None) -> object:
    ctx = _board_ctx(conn, view, request.app.state.company_groups_path)
    ctx["oob_nav"] = True
    if toast:
        ctx["toast"] = toast
    return templates.TemplateResponse(request, "board/_region.html", ctx)


@router.get("/")
def root() -> RedirectResponse:
    return RedirectResponse("/board", status_code=303)


@router.get("/review")
def legacy_review() -> RedirectResponse:
    # The review queue is gone — the Board's Inbox tiers replaced it.
    return RedirectResponse("/board", status_code=303)


@router.get("/board")
def board_page(
    request: Request,
    view: str = "board",
    show: str = "",
    conn: sqlite3.Connection = Depends(get_conn),
):
    _self_heal_tiers(conn, request.app.state.company_groups_path)
    ctx = _board_ctx(conn, view, request.app.state.company_groups_path)
    ctx["active"] = "board"
    if show == "dismissed":
        ctx["view"] = "dismissed"
        ctx["dismissed"] = store.dismissed_rows(conn)
    elif show == "flagged":
        ctx["view"] = "flagged"
        ctx["flagged"] = store.flagged_rows(conn)
    elif show == "duplicates":
        ctx["view"] = "duplicates"
        ctx["possible"] = dedup.find_possible_duplicates(conn)
        ctx["merged"] = review.merged_duplicates(conn)
    return templates.TemplateResponse(request, "board/index.html", ctx)


@router.get("/board/card/{posting_id}")
def drawer(
    request: Request,
    posting_id: str,
    view: str = "board",
    conn: sqlite3.Connection = Depends(get_conn),
):
    r = _board_row(conn, posting_id)
    payload = review.payload_summary(r["payload_path"])
    response = templates.TemplateResponse(
        request,
        "drawer/_posting.html",
        {"r": r, "view": view, "payload": payload, "tier_ui": TIER_UI},
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
    _board_row(conn, posting_id)
    try:
        store.set_application(
            conn, posting_id, status=status, notes=notes, applied_date=applied_date or None
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _refresh(conn)  # entering/leaving the pipeline is a label change
    r = _board_row(conn, posting_id)
    payload = review.payload_summary(r["payload_path"])
    ctx = _board_ctx(conn, view, request.app.state.company_groups_path)
    ctx.update({"r": r, "view": view, "payload": payload, "tier_ui": TIER_UI,
                "oob_board": True, "saved": True})
    return templates.TemplateResponse(request, "drawer/_posting.html", ctx)


@router.post("/board/move")
def move(
    request: Request,
    posting_id: str = Form(...),
    status: str = Form(...),
    undo: str = Form(""),
    conn: sqlite3.Connection = Depends(get_conn),
):
    prev = store.get_application(conn, posting_id)
    prev_status = (prev["status"] if prev else None) or "Untracked"
    r = _board_row(conn, posting_id)
    try:
        store.set_application_status(
            conn,
            posting_id,
            status,
            applied_date_if_empty=clock.now_iso()[:10] if status == "Applied" else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _refresh(conn)  # applying (or un-applying) is a training label
    toast = None
    if not undo:
        toast = {
            "text": f"Moved to {status} — {r['company']}: {r['title']}",
            "undo_url": "/board/move",
            "fields": {"posting_id": posting_id, "status": prev_status, "undo": "1"},
        }
    return _region(request, conn, toast=toast)


@router.post("/board/dismiss")
def dismiss(
    request: Request,
    posting_id: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    r = _board_row(conn, posting_id)
    review.dismiss(conn, posting_id, review.DISMISS_REASON, now=clock.now_iso())
    _refresh(conn)
    toast = {
        "text": f"Dismissed — {r['company']}: {r['title']}",
        "undo_url": "/board/undo-dismiss",
        "fields": {"posting_id": posting_id},
    }
    response = _region(request, conn, toast=toast)
    response.headers["HX-Trigger"] = "drawer-close"  # dismissing from the drawer closes it
    return response


@router.post("/board/undo-dismiss")
def undo_dismiss(
    request: Request,
    posting_id: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    review.undo_dismiss(conn, posting_id)
    _refresh(conn)
    return _region(request, conn)


@router.post("/board/flag")
def flag_posting(
    request: Request,
    posting_id: str = Form(...),
    reason: str = Form(""),
    conn: sqlite3.Connection = Depends(get_conn),
):
    r = _board_row(conn, posting_id)
    review.flag(conn, posting_id, reason.strip(), now=clock.now_iso())
    _refresh(conn)  # the row left the inbox scope; keep scores/tiers honest
    toast = {
        "text": f"Flagged for review — {r['company']}: {r['title']}",
        "undo_url": "/board/unflag",
        "fields": {"posting_id": posting_id},
    }
    response = _region(request, conn, toast=toast)
    response.headers["HX-Trigger"] = "drawer-close"  # flagging from the drawer closes it
    return response


@router.post("/board/unflag")
def unflag_posting(
    request: Request,
    posting_id: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    # Flagged rows aren't in the inbox query — validate against the flagged queue.
    if not any(r["posting_id"] == posting_id for r in store.flagged_rows(conn, limit=100000)):
        raise HTTPException(status_code=404, detail=f"no flagged posting {posting_id!r}")
    review.unflag(conn, posting_id)
    _refresh(conn)
    return _region(request, conn)


def _dedup_region(request: Request, conn: sqlite3.Connection, *, toast: dict | None = None):
    """Re-render the Duplicates lens region so the user can keep working the queue."""
    ctx = _board_ctx(conn, "board", request.app.state.company_groups_path)
    ctx["view"] = "duplicates"
    ctx["possible"] = dedup.find_possible_duplicates(conn)
    ctx["merged"] = review.merged_duplicates(conn)
    ctx["oob_nav"] = True
    if toast:
        ctx["toast"] = toast
    return templates.TemplateResponse(request, "board/_region.html", ctx)


@router.post("/board/dedup/confirm")
def dedup_confirm(
    request: Request,
    survivor_id: str = Form(...),
    posting_ids: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    ids = [pid for pid in posting_ids.split(",") if pid]
    n = review.confirm_duplicates(conn, survivor_id, ids)
    _refresh(conn)
    toast = {
        "text": f"Merged {n} duplicate{'' if n == 1 else 's'} — kept 1",
        "undo_url": "/board?show=duplicates", "fields": {},
    } if n else None
    return _dedup_region(request, conn, toast=toast)


@router.post("/board/dedup/keep")
def dedup_keep(
    request: Request,
    posting_ids: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    ids = [pid for pid in posting_ids.split(",") if pid]
    review.keep_separate(conn, ids)
    _refresh(conn)
    return _dedup_region(request, conn)


@router.post("/board/dedup/undo")
def dedup_undo(
    request: Request,
    posting_id: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    review.undo_duplicate(conn, posting_id)
    _refresh(conn)
    return _dedup_region(request, conn)


@router.post("/board/company-group")
def set_company_group(
    request: Request,
    company: str = Form(...),
    group: str = Form(...),
    reason: str = Form(""),
    approved_restore: str = Form(""),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Move the whole company, never an individual posting, between groups."""
    previous, _entry = companygroups.classify(
        company, tiers.load_tier_map(request.app.state.company_groups_path)
    )
    if group == "discovery" and not approved_restore:
        raise HTTPException(
            status_code=400,
            detail="discovery requires a reason/evidence proposal and approval",
        )
    try:
        if group == "unclassified":
            companygroups.clear_group(request.app.state.company_groups_path, company)
        else:
            companygroups.set_group(
                request.app.state.company_groups_path,
                company,
                group,
                reason=reason,
                allow_discovery=bool(approved_restore),
            )
    except (ValueError, config.ConfigError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    tiers.retier_inbox(conn, tiers.load_tier_map(request.app.state.company_groups_path))
    return _region(
        request, conn,
        toast={
            "text": f"{company} moved to {tiers.TIER_LABELS[group]}",
            "undo_url": "/board/company-group",
            "fields": {
                "company": company,
                "group": previous,
                "reason": _entry.reason if _entry else "",
                "approved_restore": "1" if previous == "discovery" else "",
            },
        },
    )


@router.post("/board/pin")
def pin(
    request: Request,
    posting_id: str = Form(...),
    tier: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    r = _board_row(conn, posting_id)
    was_pinned = r["pinned_tier"]
    try:
        # A pipeline card dragged back into a tier section re-enters the Inbox first.
        if (r["status"] or "Untracked") in PIPELINE_LANES:
            store.set_application_status(conn, posting_id, "Untracked")
        review.pin_tier(conn, posting_id, tier, now=clock.now_iso())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _refresh(conn)
    toast = {
        "text": f"Pinned to {tiers.TIER_LABELS[tier]} — {r['title']}",
        "undo_url": "/board/unpin",
        "fields": {"posting_id": posting_id},
        "undo_label": "Unpin",
    } if not was_pinned else None
    return _region(request, conn, toast=toast)


@router.post("/board/unpin")
def unpin(
    request: Request,
    posting_id: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    review.unpin(conn, posting_id)
    _refresh(conn)
    return _region(request, conn)


@router.post("/board/lane")
def toggle_lane(
    request: Request,
    lane: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    if lane not in PIPELINE_LANES:
        raise HTTPException(status_code=400, detail=f"unknown lane {lane!r}")
    hidden = _hidden_lanes(conn) ^ {lane}
    db.set_meta(conn, HIDDEN_LANES_KEY, json.dumps(sorted(hidden)))
    # Display state only — no rescore/retier, nothing about the postings changed.
    return _region(request, conn)


@router.post("/board/hygiene-closed")
def hygiene_closed(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    now = clock.now_iso()
    n = review.clear_closed_inbox(conn, now=now)
    _refresh(conn)
    toast = {
        "text": f"Dismissed {n} closed posting{'' if n == 1 else 's'}",
        "undo_url": "/board/hygiene-undo",
        "fields": {"reviewed_at": now, "reason": review.CLOSED_REASON},
    } if n else None
    return _region(request, conn, toast=toast)


@router.post("/board/hygiene-leaks")
def hygiene_leaks(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    entries, _ = config.load_sources(
        config.default_path("INTERNSHELPER_SOURCES", "config/sources.yaml")
    )
    now = clock.now_iso()
    n = review.clear_guard_leaks(conn, entries, now=now)
    _refresh(conn)
    toast = {
        "text": f"Dismissed {n} guard leak{'' if n == 1 else 's'}",
        "undo_url": "/board/hygiene-undo",
        "fields": {"reviewed_at": now, "reason": review.GUARD_LEAK_REASON},
    } if n else None
    return _region(request, conn, toast=toast)


@router.post("/board/hygiene-undo")
def hygiene_undo(
    request: Request,
    reviewed_at: str = Form(...),
    reason: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    review.undo_bulk_clear(conn, reviewed_at, reason)
    _refresh(conn)
    return _region(request, conn)
