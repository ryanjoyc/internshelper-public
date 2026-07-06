"""Unit tests for the bootstrap helper (internshelper.setup).

Covers the pure, safe-to-test pieces: the gitignore guard (never write secrets to a tracked
.env), .env rendering + scaffold (write-once, never clobber), and plist realization (no
plaintext password, no leftover placeholders). The interactive prompts and launchctl calls
are exercised manually.
"""

from pathlib import Path

from internshelper import setup

REPO = Path(__file__).resolve().parent.parent


def test_render_env_has_all_keys_and_reflects_flags():
    out = setup.render_env("a@x", "b@x", "pw", email=False, schedule=False, dashboard=True,
                           app=False)
    for key in ("INTERNSHELPER_SMTP_SENDER", "INTERNSHELPER_SMTP_RECIPIENT",
                "INTERNSHELPER_SMTP_PASSWORD", "INTERNSHELPER_FEATURE_COLLECT",
                "INTERNSHELPER_FEATURE_EMAIL", "INTERNSHELPER_FEATURE_SCHEDULE",
                "INTERNSHELPER_FEATURE_DASHBOARD", "INTERNSHELPER_FEATURE_APP"):
        assert key in out
    assert "INTERNSHELPER_FEATURE_EMAIL=0" in out
    assert "INTERNSHELPER_FEATURE_SCHEDULE=0" in out
    assert "INTERNSHELPER_FEATURE_DASHBOARD=1" in out
    assert "INTERNSHELPER_FEATURE_APP=0" in out
    assert "INTERNSHELPER_SMTP_SENDER=a@x" in out


def test_prompt_uses_getpass_for_password(monkeypatch):
    # The password must NOT be read via echoing input(); _prompt uses getpass.
    answers = iter(["y", "s@x", "r@x", "n", "n"])  # email? sender recipient schedule? dashboard?
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    monkeypatch.setattr(setup.getpass, "getpass", lambda *a: "SECRET-PW")
    email, schedule, dashboard, app, sender, recipient, password = setup._prompt()
    assert email is True and password == "SECRET-PW"
    assert sender == "s@x" and recipient == "r@x"
    assert schedule is False and dashboard is False
    assert app is False  # dashboard off short-circuits the Dock-app question


def test_prompt_asks_dock_app_only_with_dashboard(monkeypatch):
    # email? schedule? dashboard? dock-app?  (email off, so no SMTP inputs)
    answers = iter(["n", "n", "y", "y"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    email, schedule, dashboard, app, *_ = setup._prompt()
    assert dashboard is True and app is True


def test_ensure_gitignore_true_for_real_repo():
    assert setup.ensure_gitignore_has_env(REPO / ".gitignore") is True


def test_ensure_gitignore_false_when_env_missing(tmp_path):
    (tmp_path / ".gitignore").write_text(".venv/\ndata/\n")
    assert setup.ensure_gitignore_has_env(tmp_path / ".gitignore") is False


def test_ensure_gitignore_false_when_file_missing(tmp_path):
    assert setup.ensure_gitignore_has_env(tmp_path / "nope") is False


def test_scaffold_env_writes_when_absent(tmp_path):
    p = tmp_path / ".env"
    assert setup.scaffold_env(p, "a@x", "b@x", "pw",
                              email=True, schedule=True, dashboard=True) is True
    assert "INTERNSHELPER_SMTP_SENDER=a@x" in p.read_text()


def test_scaffold_env_noop_when_present(tmp_path):
    p = tmp_path / ".env"
    p.write_text("EXISTING=secret\n")
    assert setup.scaffold_env(p, "a@x", "b@x", "pw",
                              email=True, schedule=True, dashboard=True) is False
    assert p.read_text() == "EXISTING=secret\n"  # never clobbered


def test_realize_plist_substitutes_repo_dir(tmp_path):
    tmpl = tmp_path / "tmpl.plist"
    tmpl.write_text("<string>__REPO_DIR__/.venv/bin/python</string>\n")
    dest = tmp_path / "out" / "com.x.plist"
    setup.realize_plist(tmpl, dest, "/repo/here")
    out = dest.read_text()
    assert "/repo/here/.venv/bin/python" in out
    assert "__" not in out


def test_real_plist_template_has_no_password():
    text = (REPO / "scripts" / "com.internshelper.run.plist.template").read_text()
    assert "__SMTP_APP_PASSWORD__" not in text
    assert "INTERNSHELPER_SMTP_PASSWORD" not in text
    assert "__REPO_DIR__" in text  # repo dir still templated


def test_main_no_input_email_off_skips_creds(tmp_path, monkeypatch):
    (tmp_path / ".gitignore").write_text(".env\n")
    monkeypatch.setenv("INTERNSHELPER_FEATURE_EMAIL", "0")
    monkeypatch.setenv("INTERNSHELPER_FEATURE_SCHEDULE", "0")
    monkeypatch.setenv("INTERNSHELPER_FEATURE_APP", "0")
    monkeypatch.setenv("INTERNSHELPER_SMTP_PASSWORD", "should-not-be-written")
    rc = setup.main(["--no-input", "--repo-dir", str(tmp_path)])
    assert rc == 0
    env = (tmp_path / ".env").read_text()
    assert "INTERNSHELPER_FEATURE_EMAIL=0" in env
    assert "should-not-be-written" not in env       # creds skipped when email off
    assert "INTERNSHELPER_SMTP_PASSWORD=" in env     # key present but empty


def test_main_no_input_email_on_writes_creds(tmp_path, monkeypatch):
    (tmp_path / ".gitignore").write_text(".env\n")
    monkeypatch.setenv("INTERNSHELPER_FEATURE_EMAIL", "1")
    monkeypatch.setenv("INTERNSHELPER_FEATURE_SCHEDULE", "0")
    monkeypatch.setenv("INTERNSHELPER_FEATURE_APP", "0")
    monkeypatch.setenv("INTERNSHELPER_SMTP_SENDER", "me@x.com")
    monkeypatch.setenv("INTERNSHELPER_SMTP_RECIPIENT", "you@x.com")
    monkeypatch.setenv("INTERNSHELPER_SMTP_PASSWORD", "secret-pw")
    rc = setup.main(["--no-input", "--repo-dir", str(tmp_path)])
    assert rc == 0
    env = (tmp_path / ".env").read_text()
    assert "INTERNSHELPER_SMTP_SENDER=me@x.com" in env
    assert "INTERNSHELPER_SMTP_RECIPIENT=you@x.com" in env
    assert "INTERNSHELPER_SMTP_PASSWORD=secret-pw" in env
    assert "INTERNSHELPER_FEATURE_EMAIL=1" in env


def test_main_aborts_when_env_not_gitignored(tmp_path):
    (tmp_path / ".gitignore").write_text(".venv/\n")  # no .env
    rc = setup.main(["--no-input", "--repo-dir", str(tmp_path)])
    assert rc == 1
    assert not (tmp_path / ".env").exists()
