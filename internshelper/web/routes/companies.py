"""Companies page: the approved-companies index + Approval A (board proposals).

One card per company (name · state · board or proposal + evidence). Approve/reject act
on agent-written proposals; all writes go through internshelper.companies — UI only.
Dream-tier toggle promotes a company's postings to the Board's Apply-first tier.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request

from internshelper import companies, config
from internshelper.web.deps import get_conn, nav_context
from internshelper.web.templating import templates

router = APIRouter()


def _ctx(request: Request, flash: str | None = None) -> dict:
    entries, errors = companies.load_companies(request.app.state.companies_path)
    try:
        src_entries, _ = config.load_sources(request.app.state.sources_path)
        source_keys = {e.source_key for e in src_entries}
    except config.ConfigError:
        source_keys = set()
    groups = {s: [e for e in entries if e.status == s] for s in companies.STATUSES}
    return {"groups": groups, "total": len(entries), "errors": errors,
            "dangling": companies.dangling(entries, source_keys), "flash": flash}


def _list(request: Request, flash: str | None = None):
    return templates.TemplateResponse(request, "companies/_list.html",
                                      _ctx(request, flash))


@router.get("/companies")
def companies_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    return templates.TemplateResponse(
        request, "companies/index.html",
        {"nav": nav_context(conn), "active": "companies", **_ctx(request)},
    )


@router.post("/companies/add")
def add_company(request: Request, name: str = Form(...)):
    try:
        companies.add_company(request.app.state.companies_path, name)
        flash = f"added {name}"
    except (ValueError, config.ConfigError) as e:
        flash = str(e)
    return _list(request, flash)


@router.post("/companies/approve")
def approve(request: Request, name: str = Form(...)):
    try:
        src = companies.approve(request.app.state.companies_path,
                                request.app.state.sources_path, name)
        flash = f"{name} resolved -> {src.source_key}"
    except (ValueError, config.ConfigError) as e:
        flash = str(e)
    return _list(request, flash)


@router.post("/companies/set-tier")
def set_tier(request: Request, name: str = Form(...), tier: str = Form("")):
    try:
        companies.set_tier(request.app.state.companies_path, name, tier)
        flash = f"{name} -> {'dream (Apply first)' if tier == 'dream' else 'default tier'}"
    except (ValueError, config.ConfigError) as e:
        flash = str(e)
    return _list(request, flash)


@router.post("/companies/reject")
def reject(request: Request, name: str = Form(...), notes: str = Form("")):
    try:
        companies.reject(request.app.state.companies_path, name, notes=notes)
        flash = f"{name} -> no-board"
    except (ValueError, config.ConfigError) as e:
        flash = str(e)
    return _list(request, flash)
