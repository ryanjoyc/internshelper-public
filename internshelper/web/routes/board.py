"""The Board: the whole product on one page.

There is no approval gate — every collected posting lands in the first column (the
Inbox, grouped by company-priority sections), and the pipeline lanes to its right
track what the user acted on. Dragging a card Inbox → Applied IS the apply action;
Dismiss hides it (undoable). Every mutation ends with a best-effort rescore+retier —
each action is a new ranking label.

Drag moves omit notes and preserve them through set_application_status. Quick
pipeline controls use the same path but atomically include their full notes value;
the drawer's Save remains the full three-column upsert.
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
BOARD_PAGE_SIZE = 100
DUPLICATE_GROUP_PAGE_SIZE = 25

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


def _page_window(page: int, total: int, per_page: int) -> tuple[int, int, int]:
    """Return a clamped page, page count, and zero-based offset."""
    pages = max(1, -(-total // per_page))
    page = min(max(page, 1), pages)
    return page, pages, (page - 1) * per_page


def _normalize_view(view: str) -> str:
    return view if view in {"board", "table", "dismissed", "flagged", "duplicates"} else "board"


def _populate_view(
    ctx: dict,
    conn: sqlite3.Connection,
    view: str,
    *,
    page: int = 1,
    possible_page: int = 1,
    merged_page: int = 1,
    kept_page: int = 1,
) -> None:
    """Attach only the bounded rows needed by the requested Board lens."""
    view = _normalize_view(view)
    ctx["view"] = view
    ctx["page"] = page
    ctx["possible_page"] = possible_page
    ctx["merged_page"] = merged_page
    ctx["kept_page"] = kept_page
    if view == "table":
        table_rows = [
            {"row": row, "group": group["group"]}
            for group in ctx["inbox_groups"]
            for row in group["rows"]
        ]
        table_rows.extend(
            {"row": row, "group": "pipeline"}
            for lane in PIPELINE_LANES
            for row in ctx["columns"][lane]
        )
        page, pages, offset = _page_window(page, len(table_rows), BOARD_PAGE_SIZE)
        ctx.update({
            "page": page,
            "pages": pages,
            "table_total": len(table_rows),
            "table_rows": table_rows[offset : offset + BOARD_PAGE_SIZE],
        })
    elif view == "dismissed":
        total = store.dismissed_count(conn)
        page, pages, offset = _page_window(page, total, BOARD_PAGE_SIZE)
        ctx.update({
            "page": page,
            "pages": pages,
            "dismissed_total": total,
            "dismissed": store.dismissed_rows(conn, BOARD_PAGE_SIZE, offset),
        })
    elif view == "flagged":
        total = store.flagged_count(conn)
        page, pages, offset = _page_window(page, total, BOARD_PAGE_SIZE)
        ctx.update({
            "page": page,
            "pages": pages,
            "flagged_total": total,
            "flagged": store.flagged_rows(conn, BOARD_PAGE_SIZE, offset),
        })
    elif view == "duplicates":
        possible = dedup.find_possible_duplicates(conn)
        possible_page, possible_pages, possible_offset = _page_window(
            possible_page, len(possible), DUPLICATE_GROUP_PAGE_SIZE
        )
        merged_total = review.merged_duplicates_count(conn)
        merged_page, merged_pages, merged_offset = _page_window(
            merged_page, merged_total, BOARD_PAGE_SIZE
        )
        kept = dedup.find_kept_separate(conn)
        kept_page, kept_pages, kept_offset = _page_window(
            kept_page, len(kept), DUPLICATE_GROUP_PAGE_SIZE
        )
        ctx.update({
            "possible_total": len(possible),
            "possible_page": possible_page,
            "possible_pages": possible_pages,
            "possible": possible[
                possible_offset : possible_offset + DUPLICATE_GROUP_PAGE_SIZE
            ],
            "merged_total": merged_total,
            "merged_page": merged_page,
            "merged_pages": merged_pages,
            "merged": review.merged_duplicates(
                conn, BOARD_PAGE_SIZE, merged_offset
            ),
            "kept_total": len(kept),
            "kept_page": kept_page,
            "kept_pages": kept_pages,
            "kept": kept[kept_offset : kept_offset + DUPLICATE_GROUP_PAGE_SIZE],
        })


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


def _region(
    request: Request,
    conn: sqlite3.Connection,
    *,
    view: str = "board",
    page: int = 1,
    possible_page: int = 1,
    merged_page: int = 1,
    kept_page: int = 1,
    toast: dict | None = None,
) -> object:
    ctx = _board_ctx(conn, view, request.app.state.company_groups_path)
    _populate_view(
        ctx,
        conn,
        view,
        page=page,
        possible_page=possible_page,
        merged_page=merged_page,
        kept_page=kept_page,
    )
    ctx["oob_nav"] = True
    if toast:
        ctx["toast"] = toast
    return templates.TemplateResponse(request, "board/_region.html", ctx)


@router.get("/")
def root() -> RedirectResponse:
    return RedirectResponse("/board", status_code=303)


@router.get("/review")
def legacy_review() -> RedirectResponse:
    # The old Review page is gone; the company-grouped Board replaced it.
    return RedirectResponse("/board", status_code=303)


@router.get("/board")
def board_page(
    request: Request,
    view: str = "board",
    show: str = "",
    page: int = 1,
    possible_page: int = 1,
    merged_page: int = 1,
    kept_page: int = 1,
    conn: sqlite3.Connection = Depends(get_conn),
):
    _self_heal_tiers(conn, request.app.state.company_groups_path)
    ctx = _board_ctx(conn, view, request.app.state.company_groups_path)
    ctx["active"] = "board"
    requested_view = show if show in {"dismissed", "flagged", "duplicates"} else view
    _populate_view(
        ctx,
        conn,
        requested_view,
        page=page,
        possible_page=possible_page,
        merged_page=merged_page,
        kept_page=kept_page,
    )
    return templates.TemplateResponse(request, "board/index.html", ctx)


@router.get("/board/card/{posting_id}")
def drawer(
    request: Request,
    posting_id: str,
    view: str = "board",
    page: int = 1,
    conn: sqlite3.Connection = Depends(get_conn),
):
    r = _board_row(conn, posting_id)
    payload = review.payload_summary(r["payload_path"])
    response = templates.TemplateResponse(
        request,
        "drawer/_posting.html",
        {"r": r, "view": _normalize_view(view), "page": page,
         "payload": payload, "tier_ui": TIER_UI},
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
    page: int = Form(1),
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
    _populate_view(ctx, conn, view, page=page)
    ctx.update({"r": r, "view": view, "payload": payload, "tier_ui": TIER_UI,
                "page": page, "oob_board": True, "saved": True})
    return templates.TemplateResponse(request, "drawer/_posting.html", ctx)


@router.post("/board/move")
async def move(
    request: Request,
    posting_id: str = Form(...),
    status: str = Form(...),
    notes: str | None = Form(None),
    notes_null: str = Form(""),
    applied_date: str | None = Form(None),
    applied_date_null: str = Form(""),
    status_null: str = Form(""),
    application_absent: str = Form(""),
    expected_status: str = Form(""),
    expected_notes: str | None = Form(None),
    expected_notes_null: str = Form(""),
    expected_applied_date: str | None = Form(None),
    expected_applied_date_null: str = Form(""),
    undo: str = Form(""),
    return_view: str = Form("board"),
    page: int = Form(1),
    conn: sqlite3.Connection = Depends(get_conn),
):
    # FastAPI normalizes an explicitly empty optional form string to None.  Inspect
    # raw field presence so `notes=` means "clear notes", while a drag request that
    # omits notes continues to preserve them.
    raw_form = await request.form()
    notes_submitted = "notes" in raw_form
    submitted_notes = str(raw_form["notes"]) if notes_submitted else None
    applied_date_submitted = "applied_date" in raw_form
    submitted_applied_date = (
        str(raw_form["applied_date"]) if applied_date_submitted else None
    )
    expected_notes_submitted = "expected_notes" in raw_form
    submitted_expected_notes = (
        str(raw_form["expected_notes"]) if expected_notes_submitted else None
    )
    expected_applied_date_submitted = "expected_applied_date" in raw_form
    submitted_expected_applied_date = (
        str(raw_form["expected_applied_date"])
        if expected_applied_date_submitted else None
    )
    prev = store.get_application(conn, posting_id)
    raw_prev_status = prev["status"] if prev else None
    # The schema historically allowed free text. Once an old value is changed through
    # the current UI, its Undo returns to the canonical Inbox state instead of letting
    # untrusted form data reintroduce an invalid status.
    prev_status = (
        raw_prev_status if raw_prev_status in store.STATUS_OPTIONS else "Untracked"
    )
    prev_notes = prev["notes"] if prev else None
    r = _board_row(conn, posting_id)
    if undo and not expected_status:
        raise HTTPException(
            status_code=400,
            detail="Undo requires the application state it was created from",
        )
    if notes_submitted and not undo and status == prev_status:
        raise HTTPException(status_code=400, detail="choose a different application status")
    restore_notes = (
        None if notes_null else
        submitted_notes if notes_submitted else
        (prev["notes"] if prev else None)
    )
    restore_applied_date = (
        None if applied_date_null else
        submitted_applied_date if applied_date_submitted else
        (prev["applied_date"] if prev else None)
    )
    try:
        if undo:
            restored = store.restore_application_if_unchanged(
                conn,
                posting_id,
                expected_status=expected_status,
                expected_notes=(
                    None if expected_notes_null else submitted_expected_notes
                ),
                expected_applied_date=(
                    None if expected_applied_date_null else
                    submitted_expected_applied_date
                ),
                restore_exists=not bool(application_absent),
                restore_status=None if status_null else status,
                restore_notes=restore_notes,
                restore_applied_date=restore_applied_date,
            )
            if not restored:
                raise HTTPException(
                    status_code=409,
                    detail="application changed since this Undo action was created",
                )
        else:
            store.set_application_status(
                conn,
                posting_id,
                status,
                applied_date_if_empty=(
                    clock.now_iso()[:10] if status == "Applied" else None
                ),
                notes=submitted_notes,
                update_notes=notes_submitted,
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _refresh(conn)  # applying (or un-applying) is a training label
    toast = None
    if not undo:
        current = store.get_application(conn, posting_id)
        assert current is not None
        undo_fields = {
            "posting_id": posting_id,
            "status": prev_status,
            "undo": "1",
            "return_view": _normalize_view(return_view),
            "page": page,
            "expected_status": current["status"],
        }
        if current["notes"] is None:
            undo_fields["expected_notes_null"] = "1"
        else:
            undo_fields["expected_notes"] = current["notes"]
        if current["applied_date"] is None:
            undo_fields["expected_applied_date_null"] = "1"
        else:
            undo_fields["expected_applied_date"] = current["applied_date"]
        if prev is None:
            undo_fields["application_absent"] = "1"
        else:
            if prev["status"] is None:
                undo_fields["status_null"] = "1"
            if prev_notes is None:
                undo_fields["notes_null"] = "1"
            else:
                undo_fields["notes"] = prev_notes
            if prev["applied_date"] is None:
                undo_fields["applied_date_null"] = "1"
            else:
                undo_fields["applied_date"] = prev["applied_date"]
        toast = {
            "text": f"Moved to {status} — {r['company']}: {r['title']}",
            "undo_url": "/board/move",
            "fields": undo_fields,
        }
    return _region(
        request, conn, view=_normalize_view(return_view), page=page, toast=toast
    )


@router.post("/board/dismiss")
def dismiss(
    request: Request,
    posting_id: str = Form(...),
    return_view: str = Form("board"),
    page: int = Form(1),
    conn: sqlite3.Connection = Depends(get_conn),
):
    r = _board_row(conn, posting_id)
    review.dismiss(conn, posting_id, review.DISMISS_REASON, now=clock.now_iso())
    _refresh(conn)
    toast = {
        "text": f"Dismissed — {r['company']}: {r['title']}",
        "undo_url": "/board/undo-dismiss",
        "fields": {
            "posting_id": posting_id,
            "return_view": _normalize_view(return_view),
            "page": page,
        },
    }
    response = _region(
        request, conn, view=_normalize_view(return_view), page=page, toast=toast
    )
    response.headers["HX-Trigger"] = "drawer-close"  # dismissing from the drawer closes it
    return response


@router.post("/board/undo-dismiss")
def undo_dismiss(
    request: Request,
    posting_id: str = Form(...),
    return_view: str = Form("board"),
    page: int = Form(1),
    conn: sqlite3.Connection = Depends(get_conn),
):
    review.undo_dismiss(conn, posting_id)
    _refresh(conn)
    return _region(request, conn, view=_normalize_view(return_view), page=page)


@router.post("/board/flag")
def flag_posting(
    request: Request,
    posting_id: str = Form(...),
    reason: str = Form(""),
    return_view: str = Form("board"),
    page: int = Form(1),
    conn: sqlite3.Connection = Depends(get_conn),
):
    r = _board_row(conn, posting_id)
    review.flag(conn, posting_id, reason.strip(), now=clock.now_iso())
    _refresh(conn)  # the row left the inbox scope; keep scores/tiers honest
    toast = {
        "text": f"Flagged for review — {r['company']}: {r['title']}",
        "undo_url": "/board/unflag",
        "fields": {
            "posting_id": posting_id,
            "return_view": _normalize_view(return_view),
            "page": page,
        },
    }
    response = _region(
        request, conn, view=_normalize_view(return_view), page=page, toast=toast
    )
    response.headers["HX-Trigger"] = "drawer-close"  # flagging from the drawer closes it
    return response


@router.post("/board/unflag")
def unflag_posting(
    request: Request,
    posting_id: str = Form(...),
    return_view: str = Form("board"),
    page: int = Form(1),
    conn: sqlite3.Connection = Depends(get_conn),
):
    # Flagged rows aren't in the inbox query — validate against the flagged queue.
    if not any(r["posting_id"] == posting_id for r in store.flagged_rows(conn, limit=100000)):
        raise HTTPException(status_code=404, detail=f"no flagged posting {posting_id!r}")
    review.unflag(conn, posting_id)
    _refresh(conn)
    return _region(request, conn, view=_normalize_view(return_view), page=page)


def _dedup_region(
    request: Request,
    conn: sqlite3.Connection,
    *,
    possible_page: int = 1,
    merged_page: int = 1,
    kept_page: int = 1,
    toast: dict | None = None,
):
    """Re-render the Duplicates lens region so the user can keep working the queue."""
    ctx = _board_ctx(conn, "board", request.app.state.company_groups_path)
    _populate_view(
        ctx,
        conn,
        "duplicates",
        possible_page=possible_page,
        merged_page=merged_page,
        kept_page=kept_page,
    )
    ctx["oob_nav"] = True
    if toast:
        ctx["toast"] = toast
    return templates.TemplateResponse(request, "board/_region.html", ctx)


@router.post("/board/dedup/confirm")
def dedup_confirm(
    request: Request,
    survivor_id: str = Form(...),
    posting_ids: str = Form(...),
    possible_page: int = Form(1),
    merged_page: int = Form(1),
    kept_page: int = Form(1),
    conn: sqlite3.Connection = Depends(get_conn),
):
    ids = [pid for pid in posting_ids.split(",") if pid]
    losers = [pid for pid in ids if pid != survivor_id]
    try:
        n = review.confirm_duplicates(conn, survivor_id, ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _refresh(conn)
    toast = {
        "text": f"Merged {n} duplicate{'' if n == 1 else 's'} — kept 1",
        "undo_url": "/board/dedup/undo",
        "fields": {
            "posting_ids": ",".join(losers),
            "survivor_id": survivor_id,
            "possible_page": possible_page,
            "merged_page": merged_page,
            "kept_page": kept_page,
        },
    } if n else None
    return _dedup_region(
        request,
        conn,
        possible_page=possible_page,
        merged_page=merged_page,
        kept_page=kept_page,
        toast=toast,
    )


@router.post("/board/dedup/keep")
def dedup_keep(
    request: Request,
    posting_ids: str = Form(...),
    possible_page: int = Form(1),
    merged_page: int = Form(1),
    kept_page: int = Form(1),
    conn: sqlite3.Connection = Depends(get_conn),
):
    ids = [pid for pid in posting_ids.split(",") if pid]
    try:
        n = review.keep_separate(conn, ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _refresh(conn)
    toast = {
        "text": f"Kept {n} posting{'' if n == 1 else 's'} separate",
        "undo_url": "/board/dedup/undo-keep",
        "fields": {
            "posting_ids": ",".join(ids),
            "possible_page": possible_page,
            "merged_page": merged_page,
            "kept_page": kept_page,
        },
    } if n else None
    return _dedup_region(
        request,
        conn,
        possible_page=possible_page,
        merged_page=merged_page,
        kept_page=kept_page,
        toast=toast,
    )


@router.post("/board/dedup/undo")
def dedup_undo(
    request: Request,
    posting_id: str = Form(""),
    posting_ids: str = Form(""),
    survivor_id: str = Form(""),
    possible_page: int = Form(1),
    merged_page: int = Form(1),
    kept_page: int = Form(1),
    conn: sqlite3.Connection = Depends(get_conn),
):
    ids = [pid for pid in posting_ids.split(",") if pid]
    if posting_id:
        ids.append(posting_id)
    if not ids:
        raise HTTPException(status_code=400, detail="at least one posting id is required")
    review.undo_duplicates(conn, ids, survivor_id=survivor_id or None)
    _refresh(conn)
    return _dedup_region(
        request, conn, possible_page=possible_page, merged_page=merged_page,
        kept_page=kept_page,
    )


@router.post("/board/dedup/undo-keep")
def dedup_undo_keep(
    request: Request,
    posting_ids: str = Form(""),
    possible_page: int = Form(1),
    merged_page: int = Form(1),
    kept_page: int = Form(1),
    conn: sqlite3.Connection = Depends(get_conn),
):
    ids = [pid for pid in posting_ids.split(",") if pid]
    if not ids:
        raise HTTPException(status_code=400, detail="at least one posting id is required")
    review.undo_keep_separate(conn, ids)
    _refresh(conn)
    return _dedup_region(
        request, conn, possible_page=possible_page, merged_page=merged_page,
        kept_page=kept_page,
    )


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
def hygiene_closed(
    request: Request,
    return_view: str = Form("board"),
    page: int = Form(1),
    possible_page: int = Form(1),
    merged_page: int = Form(1),
    kept_page: int = Form(1),
    conn: sqlite3.Connection = Depends(get_conn),
):
    now = clock.now_iso()
    n = review.clear_closed_inbox(conn, now=now)
    _refresh(conn)
    toast = {
        "text": f"Dismissed {n} closed posting{'' if n == 1 else 's'}",
        "undo_url": "/board/hygiene-undo",
        "fields": {
            "reviewed_at": now,
            "reason": review.CLOSED_REASON,
            "return_view": _normalize_view(return_view),
            "page": page,
            "possible_page": possible_page,
            "merged_page": merged_page,
            "kept_page": kept_page,
        },
    } if n else None
    return _region(
        request, conn, view=_normalize_view(return_view), page=page,
        possible_page=possible_page, merged_page=merged_page, kept_page=kept_page,
        toast=toast,
    )


@router.post("/board/hygiene-leaks")
def hygiene_leaks(
    request: Request,
    return_view: str = Form("board"),
    page: int = Form(1),
    possible_page: int = Form(1),
    merged_page: int = Form(1),
    kept_page: int = Form(1),
    conn: sqlite3.Connection = Depends(get_conn),
):
    entries, _ = config.load_sources(
        config.default_path("INTERNSHELPER_SOURCES", "config/sources.yaml")
    )
    now = clock.now_iso()
    n = review.clear_guard_leaks(conn, entries, now=now)
    _refresh(conn)
    toast = {
        "text": f"Dismissed {n} guard leak{'' if n == 1 else 's'}",
        "undo_url": "/board/hygiene-undo",
        "fields": {
            "reviewed_at": now,
            "reason": review.GUARD_LEAK_REASON,
            "return_view": _normalize_view(return_view),
            "page": page,
            "possible_page": possible_page,
            "merged_page": merged_page,
            "kept_page": kept_page,
        },
    } if n else None
    return _region(
        request, conn, view=_normalize_view(return_view), page=page,
        possible_page=possible_page, merged_page=merged_page, kept_page=kept_page,
        toast=toast,
    )


@router.post("/board/hygiene-undo")
def hygiene_undo(
    request: Request,
    reviewed_at: str = Form(...),
    reason: str = Form(...),
    return_view: str = Form("board"),
    page: int = Form(1),
    possible_page: int = Form(1),
    merged_page: int = Form(1),
    kept_page: int = Form(1),
    conn: sqlite3.Connection = Depends(get_conn),
):
    review.undo_bulk_clear(conn, reviewed_at, reason)
    _refresh(conn)
    return _region(
        request, conn, view=_normalize_view(return_view), page=page,
        possible_page=possible_page, merged_page=merged_page, kept_page=kept_page,
    )
