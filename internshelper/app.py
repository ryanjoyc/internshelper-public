"""Dock-app runtime launcher: web-UI server + native macOS window (pywebview).

Run as `python -m internshelper.app` (the InternsHELPer.app bundle does exactly this).
Starts the FastAPI web UI (`python -m internshelper.web`) on a dedicated port, waits
for it to come up, and shows it in a native WKWebView window. Closing the window stops
the server — unless we merely attached to one we didn't start (e.g. a manual dev run),
which is left alone.

The server runs under a pipe-watchdog (see _WATCHDOG_SRC) that kills it whenever the
launcher dies, however it dies — Cmd-Q terminates the launcher through the Cocoa runtime
without unwinding Python, so cleanup can't live only after webview.start(). A pidfile
(`data/app.pid`, holding the watchdog pid) backstops the rare case where the watchdog
itself is gone but its server survived: a healthy server plus a live pidfile pid is ours
to reap on close; a healthy server without one is someone else's and is left alone.
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
PIDFILE = _REPO_ROOT / "data" / "app.pid"
LOG_MAX_BYTES = 1_000_000  # cap data/app.log at launch so it can't grow unbounded

WINDOW_TITLE = "internsHELPer"
WINDOW_SIZE = (1280, 860)
WINDOW_MIN_SIZE = (980, 640)  # app.css sets the matching body min-width: 980px

# Ownership modes for an already-healthy server on APP_PORT.
SPAWN = "spawn"          # no server: start one and own it
ATTACH_OWN = "attach_own"    # our stale server (crashed window): attach + kill on close
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


# --- pidfile ------------------------------------------------------------------------

def read_pidfile(path: str | Path = PIDFILE) -> int | None:
    try:
        return int(Path(path).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def write_pidfile(pid: int, path: str | Path = PIDFILE) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"{pid}\n", encoding="utf-8")


def clear_pidfile(path: str | Path = PIDFILE) -> None:
    Path(path).unlink(missing_ok=True)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # A live pid we can't signal belongs to another user — pids get recycled, and a
        # server we could never reap must not be claimed as ours (shutdown would crash).
        return False
    return True


# --- decisions (pure) ---------------------------------------------------------------

def decide(healthy: bool, pidfile_pid: int | None, alive) -> str:
    """Ownership mode for the server situation on APP_PORT.

    `alive` is pid_alive, injected for testability.
    """
    if not healthy:
        return SPAWN
    if pidfile_pid is not None and alive(pidfile_pid):
        return ATTACH_OWN
    return ATTACH_FOREIGN


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


def launch_server(port: int, repo_root: Path = _REPO_ROOT) -> subprocess.Popen:
    """Start the server under the watchdog. cwd stays the repo root so relative
    data/config paths and the log land in the right place."""
    log_path = repo_root / "data" / "app.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    trim_log(log_path)
    log = open(log_path, "a", encoding="utf-8")
    return subprocess.Popen(
        watchdog_command(port, repo_root),
        cwd=repo_root, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def shutdown(pid_or_proc) -> None:
    """Stop an owned server: terminate → wait → kill. Accepts a Popen or a bare pid."""
    if isinstance(pid_or_proc, subprocess.Popen):
        pid_or_proc.terminate()
        try:
            pid_or_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pid_or_proc.kill()
    else:
        try:
            os.kill(pid_or_proc, 15)
            for _ in range(20):
                if not pid_alive(pid_or_proc):
                    break
                time.sleep(0.25)
            else:
                os.kill(pid_or_proc, 9)
        except ProcessLookupError:
            pass
    clear_pidfile()


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
    mode = decide(probe_health(port), read_pidfile(), pid_alive)
    proc = None
    owned_pid = None

    if mode == SPAWN:
        if port_in_use(port):
            # Something answers on 8510 but fails /healthz. If the pidfile says it's
            # ours (e.g. a pre-upgrade Streamlit server from before the web-UI cutover),
            # reap it and take the port back; otherwise fail loudly rather than hide the
            # app on a random port no future launch would find.
            stale = read_pidfile()
            if stale is not None and pid_alive(stale):
                shutdown(stale)
            if port_in_use(port):
                return fail(f"Port {port} is in use by another process. "
                            "Quit it (or reboot) and relaunch internsHELPer.")
        proc = launch_server(port)
        write_pidfile(proc.pid)
        if not wait_for_server(lambda: probe_health(port)):
            shutdown(proc)
            return fail("The app server did not start in time. "
                        f"See {_REPO_ROOT / 'data' / 'app.log'} for details.")
    elif mode == ATTACH_OWN:
        owned_pid = read_pidfile()

    import webview  # heavy import (pyobjc); deferred until after the checks

    webview.create_window(
        WINDOW_TITLE, f"http://127.0.0.1:{port}",
        width=WINDOW_SIZE[0], height=WINDOW_SIZE[1], min_size=WINDOW_MIN_SIZE,
    )
    webview.start()  # blocks on the main thread until the window closes

    if proc is not None:
        shutdown(proc)
    elif owned_pid is not None:
        shutdown(owned_pid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
