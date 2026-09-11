"""Unit tests for the Dock-app runtime launcher (internshelper.app).

Covers the pure, safe-to-test pieces: the web-server command line, the server poll loop
(with injected probe/clock/sleep), log capping, and the rule that only a process launched
by the current app instance may be stopped. No sockets, no subprocesses, no pywebview —
the window lifecycle is exercised manually.
"""

import os
import stat
import sys

import pytest

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


def test_log_event_keeps_log_and_data_directory_owner_only(tmp_path):
    app.log_event("server ready", tmp_path)
    data_dir = tmp_path / "data"
    if os.name == "posix":
        assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE((data_dir / "app.log").stat().st_mode) == 0o600


def test_decide_never_claims_a_preexisting_server():
    assert app.decide(False) == app.SPAWN
    assert app.decide(True) == app.ATTACH_FOREIGN


def test_shutdown_rejects_a_saved_or_recycled_pid_without_signaling(monkeypatch):
    signals = []
    monkeypatch.setattr(app.os, "kill", lambda *args: signals.append(args))

    with pytest.raises(TypeError, match="launched by this app"):
        app.shutdown(4242)

    assert signals == []
