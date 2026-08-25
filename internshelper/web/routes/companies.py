"""Companies page: browsing groups plus the board-resolution index.

One card per company (name · state · board or proposal + evidence). Approve/reject act
on agent-written proposals; all writes go through internshelper.companies — UI only.
Discovery suggestions remain proposals until the user approves them here.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request

from internshelper import companies, companygroups, config, store
from internshelper.web.deps import get_conn, nav_context
from internshelper.web.templating import templates

router = APIRouter()


def _board_group_counts(
    conn: sqlite3.Connection,
    priority_entries: list[companygroups.CompanyGroupEntry],
) -> tuple[dict[str, int], dict[str, int]]:
    """Count the company cards and postings currently visible in each Board group."""
    mapping = companygroups.group_map(priority_entries)
    companies_by_group = {key: set() for key in companygroups.ALL_GROUPS}
    posting_counts: dict[str, int] = {}
    for row in store.inbox_with_status(conn):
        if row["status"] not in (None, "Untracked"):
            continue
        raw_name = (row["company"] or "").strip()
        group, entry = companygroups.classify(raw_name, mapping)
        display_name = entry.name if entry else (raw_name or "Unknown")
        identity = companygroups.normalize_company(display_name) or "unknown"
        companies_by_group[group].add(identity)
        posting_counts[display_name] = posting_counts.get(display_name, 0) + 1
    return (
        {key: len(names) for key, names in companies_by_group.items()},
        posting_counts,
    )


def _ctx(
    request: Request,
    conn: sqlite3.Connection,
    toast: dict | None = None,
) -> dict:
    entries, errors = companies.load_companies(request.app.state.companies_path)
    priority_entries, priority_errors = companygroups.load_company_groups(
        request.app.state.company_groups_path
    )
    try:
        src_entries, _ = config.load_sources(request.app.state.sources_path)
        source_keys = {e.source_key for e in src_entries}
    except config.ConfigError:
        source_keys = set()
    groups = {s: [e for e in entries if e.status == s] for s in companies.STATUSES}
    priority_groups = {
        key: [entry for entry in priority_entries if entry.group == key]
        for key in companygroups.GROUPS
    }
    priority_proposals = [entry for entry in priority_entries if entry.proposal]
    priority_counts, posting_counts = _board_group_counts(conn, priority_entries)
    return {"groups": groups, "total": len(entries), "errors": errors,
            "priority_groups": priority_groups,
            "priority_labels": companygroups.GROUP_LABELS,
            "priority_proposals": priority_proposals,
            "priority_counts": priority_counts,
            "posting_counts": posting_counts,
            "priority_errors": priority_errors,
            "dangling": companies.dangling(entries, source_keys), "toast": toast}


def _list(request: Request, conn: sqlite3.Connection, toast: dict | None = None):
    return templates.TemplateResponse(request, "companies/_list.html",
                                      _ctx(request, conn, toast))


@router.get("/companies")
def companies_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    return templates.TemplateResponse(
        request, "companies/index.html",
        {"nav": nav_context(conn), "active": "companies", **_ctx(request, conn)},
    )


@router.post("/companies/add")
def add_company(
    request: Request,
    name: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        companies.add_company(request.app.state.companies_path, name)
        text = f"added {name}"
    except (ValueError, config.ConfigError) as e:
        text = str(e)
    return _list(request, conn, {"text": text})


@router.post("/companies/approve")
def approve(
    request: Request,
    name: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        src = companies.approve(request.app.state.companies_path,
                                request.app.state.sources_path, name)
        text = f"{name} resolved -> {src.source_key}"
    except (ValueError, config.ConfigError) as e:
        text = str(e)
    return _list(request, conn, {"text": text})


@router.post("/companies/set-tier")
def set_tier(
    request: Request,
    name: str = Form(...),
    tier: str = Form(""),
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        companies.set_tier(request.app.state.companies_path, name, tier)
        text = f"{name}: legacy tier field updated (Board groups are separate)"
    except (ValueError, config.ConfigError) as e:
        text = str(e)
    return _list(request, conn, {"text": text})


@router.post("/companies/group/set")
def set_group(
    request: Request,
    name: str = Form(...),
    group: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        entries, _errors = companygroups.load_company_groups(
            request.app.state.company_groups_path
        )
        current, _entry = companygroups.classify(name, companygroups.group_map(entries))
        if group == "discovery" and current != "discovery":
            raise ValueError("discovery requires a reason/evidence proposal and approval")
        if group == "unclassified":
            companygroups.clear_group(request.app.state.company_groups_path, name)
        else:
            companygroups.set_group(request.app.state.company_groups_path, name, group)
        text = f"{name} -> {companygroups.GROUP_LABELS[group]}"
    except (ValueError, config.ConfigError) as e:
        text = str(e)
    return _list(request, conn, {"text": text})


@router.post("/companies/group/approve")
def approve_group(
    request: Request,
    name: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        entries, _errors = companygroups.load_company_groups(
            request.app.state.company_groups_path
        )
        entry = companygroups.find(entries, name)
        if entry is None or not entry.proposal:
            raise ValueError(f"{name} has no pending company-group proposal")
        proposal = dict(entry.proposal)
        previous_group = entry.group
        previous_reason = entry.reason
        companygroups.approve_proposal(request.app.state.company_groups_path, name)
        toast = {
            "text": f"{name} approved -> Worth discovering",
            "undo_url": "/companies/group/undo-approve",
            "fields": {
                "name": name,
                "reason": proposal["reason"],
                "evidence": proposal["evidence"],
                "previous_group": previous_group,
                "previous_reason": previous_reason,
            },
        }
    except (ValueError, config.ConfigError) as e:
        toast = {"text": str(e)}
    return _list(request, conn, toast)


@router.post("/companies/group/reject")
def reject_group(
    request: Request,
    name: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        entries, _errors = companygroups.load_company_groups(
            request.app.state.company_groups_path
        )
        entry = companygroups.find(entries, name)
        if entry is None or not entry.proposal:
            raise ValueError(f"{name} has no pending company-group proposal")
        proposal = dict(entry.proposal)
        companygroups.reject_proposal(request.app.state.company_groups_path, name)
        toast = {
            "text": f"rejected discovery proposal for {name}",
            "undo_url": "/companies/group/undo-reject",
            "fields": {
                "name": name,
                "reason": proposal["reason"],
                "evidence": proposal["evidence"],
            },
        }
    except (ValueError, config.ConfigError) as e:
        toast = {"text": str(e)}
    return _list(request, conn, toast)


@router.post("/companies/group/undo-approve")
def undo_approve_group(
    request: Request,
    name: str = Form(...),
    reason: str = Form(...),
    evidence: str = Form(...),
    previous_group: str = Form(""),
    previous_reason: str = Form(""),
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        companygroups.clear_group(request.app.state.company_groups_path, name)
        if previous_group:
            companygroups.set_group(
                request.app.state.company_groups_path,
                name,
                previous_group,
                reason=previous_reason,
                allow_discovery=previous_group == "discovery",
            )
        companygroups.propose_discovery(
            request.app.state.company_groups_path,
            name,
            reason=reason,
            evidence=evidence,
        )
        text = f"restored discovery proposal for {name}"
    except (ValueError, config.ConfigError) as e:
        text = str(e)
    return _list(request, conn, {"text": text})


@router.post("/companies/group/undo-reject")
def undo_reject_group(
    request: Request,
    name: str = Form(...),
    reason: str = Form(...),
    evidence: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        companygroups.propose_discovery(
            request.app.state.company_groups_path,
            name,
            reason=reason,
            evidence=evidence,
        )
        text = f"restored discovery proposal for {name}"
    except (ValueError, config.ConfigError) as e:
        text = str(e)
    return _list(request, conn, {"text": text})


@router.post("/companies/reject")
def reject(
    request: Request,
    name: str = Form(...),
    notes: str = Form(""),
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        companies.reject(request.app.state.companies_path, name, notes=notes)
        text = f"{name} -> no-board"
    except (ValueError, config.ConfigError) as e:
        text = str(e)
    return _list(request, conn, {"text": text})
