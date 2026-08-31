"""Sources page: configured boards + the add wizard and remove action.

The wizard is stateless: the staged entry rides between steps in hidden form
fields (no server session), and /sources/add re-checks duplicates as a race
guard. Semantics mirror the sources CLI: duplicate check BEFORE the fetch-test,
sniffer fallback on undetectable URLs, "Add anyway" force gate on empty or
degenerate fetches, and no confirm at all on a failed fetch.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request

from internshelper import config, sniffer, sources
from internshelper.sourceurl import SourceDetectionError
from internshelper.web.deps import get_conn, nav_context, require_local_browser_request
from internshelper.web.templating import templates

router = APIRouter()


def load_entries(path) -> tuple[list, list, str | None]:
    """(entries, per-entry warnings, hard error) — malformed config is a banner, never a 500."""
    try:
        entries, errors = config.load_sources(path)
        return entries, errors, None
    except config.ConfigError as e:
        return [], [], str(e)


def _parse_form(label: str, tmm: str, cols: str) -> tuple[str | None, list[str] | None, dict | None]:
    tmm_list = [s.strip() for s in tmm.split(",") if s.strip()] or None
    cols_dict = sources.parse_kv(cols) or None
    return label or None, tmm_list, cols_dict


def _rebuild_entry(type_: str, token: str, label: str, tmm: str, cols: str) -> config.SourceEntry:
    label_v, tmm_list, cols_dict = _parse_form(label, tmm, cols)
    return config.SourceEntry(
        type=type_,
        token=token,
        label=label_v or "",
        title_must_match=tmm_list or [],
        columns=cols_dict or {},
    )


def _form(request: Request, **ctx):
    return templates.TemplateResponse(request, "sources/_add_form.html", ctx)


def _preview(request: Request, entry: config.SourceEntry, label: str, tmm: str, cols: str):
    try:
        count, titles, warnings = sources.fetch_test(entry, limit=8)
        fetch_error = None
    except Exception as e:  # network/HTTP failure — surface, don't 500
        count, titles, warnings, fetch_error = 0, [], [], f"{type(e).__name__}: {e}"
    needs_force = fetch_error is None and (count == 0 or bool(warnings))
    return templates.TemplateResponse(
        request,
        "sources/_preview.html",
        {
            "entry": entry,
            "count": count,
            "titles": titles,
            "warnings": warnings,
            "fetch_error": fetch_error,
            "needs_force": needs_force,
            "label": label,
            "tmm": tmm,
            "cols": cols,
        },
    )


@router.get("/sources")
def sources_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    entries, errors, cfg_error = load_entries(request.app.state.sources_path)
    return templates.TemplateResponse(
        request,
        "sources/index.html",
        {
            "nav": nav_context(conn),
            "active": "sources",
            "entries": entries,
            "errors": errors,
            "cfg_error": cfg_error,
        },
    )


@router.get("/sources/add-form")
def add_form(request: Request):
    return _form(request)


@router.post(
    "/sources/detect", dependencies=[Depends(require_local_browser_request)]
)
def detect(
    request: Request,
    url: str = Form(""),
    label: str = Form(""),
    tmm: str = Form(""),
    cols: str = Form(""),
):
    path = request.app.state.sources_path
    label_v, tmm_list, cols_dict = _parse_form(label, tmm, cols)
    try:
        entry = sources.resolve_entry(
            url, label=label_v, title_must_match=tmm_list, columns=cols_dict
        )
    except SourceDetectionError as e:
        try:
            candidates = sniffer.sniff_careers_page(url, label=label_v)
        except sniffer.SnifferError:
            candidates = []
        if candidates:
            return templates.TemplateResponse(
                request,
                "sources/_candidates.html",
                {"candidates": candidates, "label": label, "tmm": tmm, "cols": cols},
            )
        return _form(request, form_error=f"Couldn't detect a source from that URL: {e}",
                     url=url, label=label, tmm=tmm, cols=cols)

    if sources.is_duplicate(path, entry):
        return _form(request, form_error=f"Already present: {entry.source_key}",
                     url=url, label=label, tmm=tmm, cols=cols)
    return _preview(request, entry, label, tmm, cols)


@router.post(
    "/sources/candidate", dependencies=[Depends(require_local_browser_request)]
)
def candidate(
    request: Request,
    choice: str = Form(...),
    label: str = Form(""),
    tmm: str = Form(""),
    cols: str = Form(""),
):
    path = request.app.state.sources_path
    type_, _, token = choice.partition(":")
    entry = _rebuild_entry(type_, token, label, tmm, cols)
    if sources.is_duplicate(path, entry):
        return _form(request, form_error=f"Already present: {entry.source_key}")
    return _preview(request, entry, label, tmm, cols)


@router.post("/sources/add", dependencies=[Depends(require_local_browser_request)])
def add(
    request: Request,
    type: str = Form(...),
    token: str = Form(...),
    label: str = Form(""),
    tmm: str = Form(""),
    cols: str = Form(""),
    force: str = Form("0"),
    conn: sqlite3.Connection = Depends(get_conn),
):
    path = request.app.state.sources_path
    entry = _rebuild_entry(type, token, label, tmm, cols)
    if sources.is_duplicate(path, entry):  # race guard: re-check at write time
        return _form(request, form_error=f"Already present: {entry.source_key}")
    sources.append_source(path, entry)
    entries, errors, cfg_error = load_entries(path)
    return templates.TemplateResponse(
        request,
        "sources/_add_form.html",
        {
            "oob_list": True,
            "entries": entries,
            "errors": errors,
            "cfg_error": cfg_error,
            "flash": f"Added {entry.source_key}",
        },
    )


@router.post("/sources/remove", dependencies=[Depends(require_local_browser_request)])
def remove(
    request: Request,
    source_key: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    path = request.app.state.sources_path
    removed = sources.remove_source(path, source_key)
    entries, errors, cfg_error = load_entries(path)
    return templates.TemplateResponse(
        request,
        "sources/_list.html",
        {
            "entries": entries,
            "errors": errors,
            "cfg_error": cfg_error,
            "flash": f"Removed {source_key}" if removed else None,
            "flash_error": None if removed else f"Could not remove {source_key}",
        },
    )
