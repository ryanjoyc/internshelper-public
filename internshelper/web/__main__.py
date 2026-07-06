"""Serve the web UI: `python -m internshelper.web [--port 8510]`.

Owns the serving config so it can't be misconfigured from the command line:
the bind address is always 127.0.0.1 (local-only, no network exposure), and
access logging stays off — data/app.log would otherwise grow with every
HTMX request the keyboard triage flow fires.
"""

from __future__ import annotations

import argparse

import uvicorn

from internshelper.web import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m internshelper.web",
                                     description="Serve the internsHELPer web UI.")
    parser.add_argument("--port", type=int, default=8510)
    args = parser.parse_args(argv)
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
