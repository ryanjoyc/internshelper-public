"""Unit tests for the bootstrap helper (internshelper.setup).

Covers the pure, safe-to-test pieces: the gitignore guard (never write secrets to a tracked
.env), .env rendering + scaffold (write-once, never clobber), and plist realization (no
plaintext password, no leftover placeholders). The interactive prompts and launchctl calls
are exercised manually.
"""

import plistlib
import stat
import tomllib
from pathlib import Path

import pytest

from internshelper import setup

REPO = Path(__file__).resolve().parent.parent


def _write_config_examples(repo_dir: Path) -> None:
    config_dir = repo_dir / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "profile.example.md").write_text("# Example profile\n")
    (config_dir / "companies.example.yaml").write_text("companies:\n")
    (config_dir / "company-groups.example.yaml").write_text("companies:\n")
    (config_dir / "sources.example.yaml").write_text("sources:\n")


def test_render_env_has_all_keys_and_reflects_flags():
    out = setup.render_env("a@x", "b@x", "pw", email=False, schedule=False, dashboard=True,
                           app=False, availability=False)
    for key in ("INTERNSHELPER_SMTP_SENDER", "INTERNSHELPER_SMTP_RECIPIENT",
                "INTERNSHELPER_SMTP_PASSWORD", "INTERNSHELPER_FEATURE_COLLECT",
                "INTERNSHELPER_FEATURE_EMAIL", "INTERNSHELPER_FEATURE_AVAILABILITY",
                "INTERNSHELPER_FEATURE_SCHEDULE",
                "INTERNSHELPER_FEATURE_DASHBOARD", "INTERNSHELPER_FEATURE_APP"):
        assert key in out
    assert "INTERNSHELPER_FEATURE_EMAIL=0" in out
    assert "INTERNSHELPER_FEATURE_AVAILABILITY=0" in out
    assert "INTERNSHELPER_FEATURE_SCHEDULE=0" in out
    assert "INTERNSHELPER_FEATURE_DASHBOARD=1" in out
    assert "INTERNSHELPER_FEATURE_APP=0" in out
    assert "INTERNSHELPER_SMTP_SENDER=a@x" in out


def test_prompt_uses_getpass_for_password(monkeypatch):
    # The password must NOT be read via echoing input(); _prompt uses getpass.
    answers = iter(["y", "s@x", "r@x", "y", "n", "n"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    monkeypatch.setattr(setup.getpass, "getpass", lambda *a: "SECRET-PW")
    email, availability, schedule, dashboard, app, sender, recipient, password = setup._prompt()
    assert email is True and password == "SECRET-PW"
    assert availability is True
    assert sender == "s@x" and recipient == "r@x"
    assert schedule is False and dashboard is False
    assert app is False  # dashboard off short-circuits the Dock-app question


def test_prompt_asks_dock_app_only_with_dashboard(monkeypatch):
    # email? schedule? dashboard? dock-app?  (email off, so no SMTP inputs)
    answers = iter(["n", "y", "n", "y", "y"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    email, availability, schedule, dashboard, app, *_ = setup._prompt()
    assert availability is True
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
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_scaffold_env_noop_when_present(tmp_path):
    p = tmp_path / ".env"
    p.write_text("EXISTING=secret\n")
    assert setup.scaffold_env(p, "a@x", "b@x", "pw",
                              email=True, schedule=True, dashboard=True) is False
    assert p.read_text() == "EXISTING=secret\n"  # never clobbered


def test_scaffold_user_configs_copies_examples_privately_on_first_run(tmp_path):
    _write_config_examples(tmp_path)

    created = setup.scaffold_user_configs(tmp_path)

    assert {path.name for path in created} == {
        "profile.md", "companies.yaml", "company-groups.yaml", "sources.yaml",
    }
    for example_name, runtime_name in setup.USER_CONFIG_TEMPLATES:
        example = tmp_path / "config" / example_name
        runtime = tmp_path / "config" / runtime_name
        assert runtime.read_bytes() == example.read_bytes()
        assert stat.S_IMODE(runtime.stat().st_mode) == 0o600


def test_scaffold_user_configs_never_overwrites_existing_runtime_file(tmp_path):
    _write_config_examples(tmp_path)
    existing = tmp_path / "config" / "profile.md"
    existing.write_text("# My existing profile\n")

    setup.scaffold_user_configs(tmp_path)

    assert existing.read_text() == "# My existing profile\n"


def test_scaffold_user_configs_validates_all_examples_before_writing(tmp_path):
    _write_config_examples(tmp_path)
    (tmp_path / "config" / "sources.example.yaml").unlink()

    with pytest.raises(FileNotFoundError, match="sources.example.yaml"):
        setup.scaffold_user_configs(tmp_path)

    assert not any((tmp_path / "config" / name).exists()
                   for _, name in setup.USER_CONFIG_TEMPLATES)


def test_web_extra_declares_form_parser_dependency():
    project = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]

    assert any(
        dependency.startswith("python-multipart")
        for dependency in project["optional-dependencies"]["web"]
    )


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


def test_real_plist_launches_the_venv_through_an_unprotected_system_shim(tmp_path):
    realized = tmp_path / "com.internshelper.run.plist"
    setup.realize_plist(
        REPO / "scripts" / "com.internshelper.run.plist.template",
        realized,
        "/Users/test/Documents/internsHELPer",
    )

    arguments = plistlib.loads(realized.read_bytes())["ProgramArguments"]
    assert arguments[:2] == [
        "/usr/bin/env",
        "/Users/test/Documents/internsHELPer/.venv/bin/python",
    ]


def test_real_plist_writes_logs_outside_the_protected_checkout(tmp_path):
    realized = tmp_path / "com.internshelper.run.plist"
    setup.realize_plist(
        REPO / "scripts" / "com.internshelper.run.plist.template",
        realized,
        "/Users/test/Documents/internsHELPer",
    )

    plist = plistlib.loads(realized.read_bytes())
    log_dir = Path.home() / "Library" / "Logs" / "internshelper"
    assert plist["StandardOutPath"] == str(log_dir / "run.out.log")
    assert plist["StandardErrorPath"] == str(log_dir / "run.err.log")


def test_main_no_input_email_off_skips_creds(tmp_path, monkeypatch):
    (tmp_path / ".gitignore").write_text(".env\n")
    _write_config_examples(tmp_path)
    monkeypatch.setenv("INTERNSHELPER_FEATURE_EMAIL", "0")
    monkeypatch.setenv("INTERNSHELPER_FEATURE_AVAILABILITY", "0")
    monkeypatch.setenv("INTERNSHELPER_FEATURE_SCHEDULE", "0")
    monkeypatch.setenv("INTERNSHELPER_FEATURE_APP", "0")
    monkeypatch.setenv("INTERNSHELPER_SMTP_PASSWORD", "should-not-be-written")
    rc = setup.main(["--no-input", "--repo-dir", str(tmp_path)])
    assert rc == 0
    env = (tmp_path / ".env").read_text()
    assert "INTERNSHELPER_FEATURE_EMAIL=0" in env
    assert "INTERNSHELPER_FEATURE_AVAILABILITY=0" in env
    assert "should-not-be-written" not in env       # creds skipped when email off
    assert "INTERNSHELPER_SMTP_PASSWORD=" in env     # key present but empty


def test_main_no_input_email_on_writes_creds(tmp_path, monkeypatch):
    (tmp_path / ".gitignore").write_text(".env\n")
    _write_config_examples(tmp_path)
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


def test_main_no_input_uses_safe_defaults_for_external_side_effects(tmp_path, monkeypatch):
    (tmp_path / ".gitignore").write_text(".env\n")
    _write_config_examples(tmp_path)
    for name in (
        "COLLECT", "EMAIL", "SCHEDULE", "APP", "AVAILABILITY", "DASHBOARD",
    ):
        monkeypatch.delenv(f"INTERNSHELPER_FEATURE_{name}", raising=False)
    monkeypatch.setattr(
        setup, "_install_schedule", lambda *_args: pytest.fail("schedule must stay disabled")
    )
    monkeypatch.setattr(
        setup, "_install_app", lambda *_args: pytest.fail("Dock install must stay disabled")
    )

    rc = setup.main(["--no-input", "--repo-dir", str(tmp_path)])

    assert rc == 0
    env = (tmp_path / ".env").read_text()
    assert "INTERNSHELPER_FEATURE_COLLECT=1" in env
    assert "INTERNSHELPER_FEATURE_EMAIL=0" in env
    assert "INTERNSHELPER_FEATURE_SCHEDULE=0" in env
    assert "INTERNSHELPER_FEATURE_APP=0" in env
    assert "INTERNSHELPER_FEATURE_AVAILABILITY=1" in env
    assert "INTERNSHELPER_FEATURE_DASHBOARD=1" in env


def test_main_no_input_honors_explicit_collect_disable(tmp_path, monkeypatch):
    (tmp_path / ".gitignore").write_text(".env\n")
    _write_config_examples(tmp_path)
    monkeypatch.setenv("INTERNSHELPER_FEATURE_COLLECT", "0")
    monkeypatch.setenv("INTERNSHELPER_FEATURE_EMAIL", "0")
    monkeypatch.setenv("INTERNSHELPER_FEATURE_SCHEDULE", "0")
    monkeypatch.setenv("INTERNSHELPER_FEATURE_APP", "0")

    rc = setup.main(["--no-input", "--repo-dir", str(tmp_path)])

    assert rc == 0
    assert "INTERNSHELPER_FEATURE_COLLECT=0" in (tmp_path / ".env").read_text()


def test_main_aborts_when_env_not_gitignored(tmp_path):
    (tmp_path / ".gitignore").write_text(".venv/\n")  # no .env
    rc = setup.main(["--no-input", "--repo-dir", str(tmp_path)])
    assert rc == 1
    assert not (tmp_path / ".env").exists()


def test_main_aborts_without_public_config_examples_before_writing_env(tmp_path):
    (tmp_path / ".gitignore").write_text(".env\n")

    rc = setup.main(["--no-input", "--repo-dir", str(tmp_path)])

    assert rc == 1
    assert not (tmp_path / ".env").exists()
