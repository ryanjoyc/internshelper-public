"""Unit tests for the Dock-app runtime launcher (internshelper.app).

Covers the pure, safe-to-test pieces: the streamlit command line, the server poll loop
(with injected probe/clock/sleep), pidfile round-trips, and the attach/own ownership
decision. No sockets, no subprocesses, no pywebview — the window lifecycle is exercised
manually.
"""

from internshelper import app


def test_streamlit_command_flags_and_target():
    cmd = app.streamlit_command(8510, "/repo/here")
    assert cmd[1:4] == ["-m", "streamlit", "run"]
    assert cmd[4].endswith("internshelper/dashboard.py")
    assert cmd[4].startswith("/repo/here")
    assert "--server.headless=true" in cmd
    assert "--browser.gatherUsageStats=false" in cmd
    assert "--server.address=127.0.0.1" in cmd
    assert "--server.port=8510" in cmd


def test_watchdog_command_wraps_streamlit_command():
    cmd = app.watchdog_command(8510, "/repo/here")
    assert cmd[1] == "-c"
    assert "stdin.buffer.read" in cmd[2]  # the pipe watch that reaps on launcher death
    assert cmd[3:] == app.streamlit_command(8510, "/repo/here")


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
