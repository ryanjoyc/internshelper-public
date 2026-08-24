"""Unit tests for the Dock-app runtime launcher (internshelper.app).

Covers the pure, safe-to-test pieces: the web-server command line, the server poll loop
(with injected probe/clock/sleep), pidfile round-trips, log capping, and the attach/own
ownership decision. No sockets, no subprocesses, no pywebview — the window lifecycle is
exercised manually.
"""

import sys

from internshelper import app


def test_server_command_flags_and_target():
    cmd = app.server_command(8510, "/repo/here")
    assert cmd[0] == sys.executable
    assert cmd[1:3] == ["-m", "internshelper.web"]
    assert cmd[3:] == ["--port", "8510"]


def test_watchdog_command_wraps_server_command():
    cmd = app.watchdog_command(8510, "/repo/here")
    assert cmd[1] == "-c"
    assert "stdin.buffer.read" in cmd[2]  # the pipe watch that reaps on launcher death
    assert cmd[3:] == app.server_command(8510, "/repo/here")


def test_pid_alive_treats_eperm_as_not_ours(monkeypatch):
    # A recycled pid owned by another user raises EPERM on signal 0 — we could never
    # reap it, so the launcher must not claim it as its own server.
    def kill(pid, sig):
        raise PermissionError

    monkeypatch.setattr(app.os, "kill", kill)
    assert app.pid_alive(4242) is False


def test_trim_log_caps_oversized_file(tmp_path):
    p = tmp_path / "app.log"
    p.write_text("x" * 100)
    app.trim_log(p, max_bytes=10)
    assert p.stat().st_size == 0
    p.write_text("small")
    app.trim_log(p, max_bytes=10)
    assert p.read_text() == "small"  # under the cap: untouched
    app.trim_log(tmp_path / "missing.log", max_bytes=10)  # no-op on absent file


def test_log_event_appends_to_app_log(tmp_path):
    app.log_event("server ready", tmp_path)
    app.log_event("second event", tmp_path)
    lines = (tmp_path / "data" / "app.log").read_text().splitlines()
    assert lines[0].endswith(" server ready")
    assert lines[1].endswith(" second event")


def test_wait_for_server_succeeds_on_nth_probe():
    calls = iter([False, False, True])
    assert app.wait_for_server(lambda: next(calls), timeout=10,
                               sleep=lambda _: None) is True


def test_wait_for_server_times_out():
    now = [0.0]

    def clock():
        return now[0]

    def sleep(dt):
        now[0] += dt

    assert app.wait_for_server(lambda: False, timeout=5, interval=1,
                               sleep=sleep, clock=clock) is False
    assert now[0] >= 5  # the loop actually ran to the deadline


def test_pidfile_roundtrip(tmp_path):
    p = tmp_path / "app.pid"
    app.write_pidfile(12345, p)
    assert app.read_pidfile(p) == 12345
    app.clear_pidfile(p)
    assert app.read_pidfile(p) is None
    app.clear_pidfile(p)  # idempotent on a missing file


def test_read_pidfile_garbage(tmp_path):
    p = tmp_path / "app.pid"
    p.write_text("not-a-pid\n")
    assert app.read_pidfile(p) is None


def test_decide_ownership_table():
    alive = lambda pid: pid == 111  # noqa: E731
    cases = [
        # (healthy, pidfile_pid) -> mode
        ((False, None), app.SPAWN),           # nothing running
        ((False, 111), app.SPAWN),            # stale pidfile but no server: spawn fresh
        ((True, None), app.ATTACH_FOREIGN),   # someone else's dev server
        ((True, 111), app.ATTACH_OWN),        # our crashed window's server
        ((True, 222), app.ATTACH_FOREIGN),    # pidfile pid is dead: not ours anymore
    ]
    for (healthy, pid), expected in cases:
        assert app.decide(healthy, pid, alive) == expected, (healthy, pid)
