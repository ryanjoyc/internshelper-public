"""Dock-app runtime launcher: web-UI server + native macOS window (pywebview).

Run as `python -m internshelper.app` (the InternsHELPer.app bundle does exactly this).
Starts the FastAPI web UI (`python -m internshelper.web`) on a dedicated port, waits
for it to come up, and shows it in a native WKWebView window. Closing the window stops
the server — unless we merely attached to one we didn't start (e.g. a manual dev run),
which is left alone.

The server runs under a pipe-watchdog (see _WATCHDOG_SRC) that kills it whenever the
launcher dies, however it dies — Cmd-Q terminates the launcher through the Cocoa runtime
without unwinding Python, so cleanup can't live only after webview.start(). Only the live
Popen handle establishes process ownership. A server that predates this launcher is always
left alone because a saved PID can be recycled and does not prove ownership.
"""

from __future__ import annotations

import importlib.util
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
APP_PORT = 8510  # dedicated port so a manual dev run on another port never collides
LOG_MAX_BYTES = 1_000_000  # cap data/app.log at launch so it can't grow unbounded

WINDOW_TITLE = "internsHELPer"
WINDOW_SIZE = (1280, 860)
WINDOW_MIN_SIZE = (980, 640)  # app.css sets the matching body min-width: 980px

# Ownership modes for an already-healthy server on APP_PORT.
SPAWN = "spawn"          # no server: start one and own it
ATTACH_FOREIGN = "attach_foreign"  # someone else's server: attach, leave running


# --- probes -------------------------------------------------------------------------

def probe_health(port: int, timeout: float = 1.0) -> bool:
    """True iff the web UI answers its /healthz endpoint on the port."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=timeout) as resp:
            return resp.status == 200 and resp.read().strip().lower() == b"ok"
    except (urllib.error.URLError, OSError, ValueError):
        return False


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


# --- private runtime files ----------------------------------------------------------

def _private_directory(path: Path) -> None:
    if path == Path("."):
        return
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def _open_private_text(path: Path, mode: str):
    _private_directory(path.parent)
    handle = open(
        path,
        mode,
        encoding="utf-8",
        opener=lambda name, flags: os.open(name, flags, 0o600),
    )
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(handle.fileno(), 0o600)
    except BaseException:
        handle.close()
        raise
    return handle


# --- decisions (pure) ---------------------------------------------------------------

def decide(healthy: bool) -> str:
    """Start a server only when none exists; never claim a pre-existing process."""
    return ATTACH_FOREIGN if healthy else SPAWN


def server_command(port: int, repo_root: str | Path) -> list[str]:
    """The web-server command (pure; no side effects). The 127.0.0.1 bind and quiet
    logging live inside internshelper.web.__main__, not in flags."""
    return [sys.executable, "-m", "internshelper.web", "--port", str(port)]


def wait_for_server(probe, timeout: float = 30.0, interval: float = 0.25,
                    sleep=time.sleep, clock=time.monotonic) -> bool:
    """Poll `probe()` until True or `timeout` seconds elapse."""
    deadline = clock() + timeout
    while clock() < deadline:
        if probe():
            return True
        sleep(interval)
    return probe()


# --- process management -------------------------------------------------------------

# The server runs under this tiny watchdog, which holds a pipe from the launcher and kills
# the server the moment the pipe closes. Cmd-Q terminates the launcher straight through the
# Cocoa runtime (webview.start() never returns), so post-window cleanup alone can't be
# trusted — this covers quit, crash, and kill alike. SIGTERM is translated to a clean exit
# so `shutdown()` also tears the server down through the same path.
_WATCHDOG_SRC = """\
import signal, subprocess, sys
signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
proc = subprocess.Popen(sys.argv[1:])
try:
    sys.stdin.buffer.read()  # EOF = launcher died (any cause)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
"""


def watchdog_command(port: int, repo_root: str | Path) -> list[str]:
    """The watchdog-wrapped server command (pure; no side effects)."""
    return [sys.executable, "-c", _WATCHDOG_SRC, *server_command(port, repo_root)]


def trim_log(path: str | Path, max_bytes: int = LOG_MAX_BYTES) -> None:
    """Truncate the launch log once it outgrows the cap (append mode, no rotation)."""
    p = Path(path)
    try:
        if p.stat().st_size > max_bytes:
            p.write_text("", encoding="utf-8")
    except OSError:
        pass


def log_event(message: str, repo_root: str | Path = _REPO_ROOT) -> None:
    """Append a launcher lifecycle event to the Dock app log."""
    log_path = Path(repo_root) / "data" / "app.log"
    with _open_private_text(log_path, "a") as log:
        log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


def launch_server(port: int, repo_root: Path = _REPO_ROOT) -> subprocess.Popen:
    """Start the server under the watchdog. cwd stays the repo root so relative
    data/config paths and the log land in the right place."""
    log_path = repo_root / "data" / "app.log"
    _private_directory(log_path.parent)
    trim_log(log_path)
    log = _open_private_text(log_path, "a")
    return subprocess.Popen(
        watchdog_command(port, repo_root),
        cwd=repo_root, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def shutdown(proc: subprocess.Popen) -> None:
    """Stop a server process launched by this live app instance."""
    if not isinstance(proc, subprocess.Popen):
        raise TypeError("shutdown requires a process launched by this app instance")
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def fail(msg: str) -> int:
    """Report an error somewhere the user will see it — Dock launches have no terminal."""
    print(f"internsHELPer: {msg}", file=sys.stderr)
    if sys.platform == "darwin":
        subprocess.run(
            ["osascript", "-e",
             f'display alert "internsHELPer" message {_osascript_quote(msg)}'],
            capture_output=True,
        )
    return 1


def _osascript_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# --- entry point --------------------------------------------------------------------

def main() -> int:
    for mod, hint in (("fastapi", ".[web]"), ("uvicorn", ".[web]"), ("webview", ".[app]")):
        if importlib.util.find_spec(mod) is None:
            return fail(f"The '{mod}' package is missing. "
                        f"Run: .venv/bin/python -m pip install -e '{hint}'")

    port = APP_PORT
    mode = decide(probe_health(port))
    proc = None

    if mode == SPAWN:
        if port_in_use(port):
            # The process on 8510 did not pass our health check, so its ownership is
            # unknown. Never signal it based on stale local state.
            return fail(f"Port {port} is in use by another process. "
                        "Quit it (or reboot) and relaunch internsHELPer.")
        proc = launch_server(port)
        if not wait_for_server(lambda: probe_health(port)):
            shutdown(proc)
            return fail("The app server did not start in time. "
                        f"See {_REPO_ROOT / 'data' / 'app.log'} for details.")
        log_event(f"Dock app server ready on http://127.0.0.1:{port}")

    import webview  # heavy import (pyobjc); deferred until after the checks

    webview.create_window(
        WINDOW_TITLE, f"http://127.0.0.1:{port}",
        width=WINDOW_SIZE[0], height=WINDOW_SIZE[1], min_size=WINDOW_MIN_SIZE,
    )
    webview.start()  # blocks on the main thread until the window closes

    if proc is not None:
        shutdown(proc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
