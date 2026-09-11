"""FastAPI web UI: server-rendered Jinja2 pages + HTMX partials on port 8510.

`create_app()` is the only public symbol. There is deliberately NO module-level app:
env vars (INTERNSHELPER_DB / INTERNSHELPER_SOURCES) are resolved at factory call time
so tests can redirect paths per-instance, exactly like the CLIs do.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from internshelper import config, db
from internshelper.dotenv import load_dotenv

# Payloads are scraped web content rendered in a WKWebView: same-origin only, no inline
# <script> (theme bootstrap lives in static/js/theme.js), 'unsafe-eval' for Alpine.
_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'"
)


def create_app() -> FastAPI:
    load_dotenv()  # pick up INTERNSHELPER_* from the gitignored .env (real env still wins)
    db_path = config.default_path("INTERNSHELPER_DB", "data/internshelper.db")
    sources_path = config.default_path("INTERNSHELPER_SOURCES", "config/sources.yaml")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = db.connect(db_path)  # schema + migrations once at startup, not per request
        db.init_db(conn)
        tiers.repair_stale_inbox_tiers(conn, app.state.company_groups_path)
        conn.close()
        yield

    from internshelper import companies as companies_mod, companygroups, tiers

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db_path = db_path
    app.state.sources_path = sources_path
    app.state.companies_path = companies_mod.companies_path()
    app.state.company_groups_path = companygroups.company_groups_path()
    app.mount(
        "/static",
        StaticFiles(directory=Path(__file__).parent / "static"),
        name="static",
    )

    @app.middleware("http")
    async def security_headers(request, call_next):
        from internshelper.web.deps import (
            SAFE_HTTP_METHODS,
            require_local_browser_request,
            require_loopback_host,
        )

        try:
            require_loopback_host(request)
            if request.method.upper() not in SAFE_HTTP_METHODS:
                require_local_browser_request(request)
        except HTTPException as exc:
            response = JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=exc.headers,
            )
        else:
            response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", _CSP)
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    from internshelper.web.routes import all_routers

    for router in all_routers:
        app.include_router(router)
    return app
