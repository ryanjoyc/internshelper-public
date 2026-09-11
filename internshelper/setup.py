"""One-command bootstrap helper (stdlib only).

`scripts/bootstrap.sh` creates the venv + installs deps, then hands off to this module to
scaffold per-machine config: it writes a gitignored `.env` (secrets + feature toggles) and,
on macOS if scheduling is enabled, realizes + loads the launchd plist. Idempotent: an existing
`.env` is never clobbered, and re-running the launchctl steps is safe.

    python -m internshelper.setup            # interactive
    python -m internshelper.setup --no-input # use INTERNSHELPER_FEATURE_*/SMTP_* env + defaults
"""

from __future__ import annotations

import argparse
import getpass
import os
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from internshelper.dotenv import feature_enabled

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LABEL = "com.internshelper.run"
_IGNORE_MATCHES = {".env", "/.env", "*.env"}
USER_CONFIG_TEMPLATES = (
    ("profile.example.md", "profile.md"),
    ("companies.example.yaml", "companies.yaml"),
    ("company-groups.example.yaml", "company-groups.yaml"),
    ("sources.example.yaml", "sources.yaml"),
)


def launchd_log_dir(home: str | Path | None = None) -> Path:
    """Per-user launchd logs, outside macOS-protected Documents checkouts."""
    root = Path(home) if home is not None else Path.home()
    return root / "Library" / "Logs" / "internshelper"


def ensure_gitignore_has_env(gitignore_path: str | Path) -> bool:
    """True iff `.env` is ignored by the given .gitignore. Pure check (no write)."""
    p = Path(gitignore_path)
    if not p.exists():
        return False
    return any(line.strip() in _IGNORE_MATCHES for line in p.read_text(encoding="utf-8").splitlines())


def render_env(sender: str = "", recipient: str = "", password: str = "", *,
               collect: bool = True, email: bool = False, schedule: bool = False,
               dashboard: bool = True, app: bool = True,
               availability: bool = True) -> str:
    """Render the per-machine `.env` (secrets + feature toggles)."""
    flag = lambda b: "1" if b else "0"  # noqa: E731
    return "\n".join([
        "# internsHELPer per-machine secrets + feature toggles (gitignored).",
        "# A real environment variable always overrides the value here.",
        "",
        "# --- email (SMTP) ---",
        f"INTERNSHELPER_SMTP_SENDER={sender}",
        f"INTERNSHELPER_SMTP_RECIPIENT={recipient}",
        f"INTERNSHELPER_SMTP_PASSWORD={password}",
        "",
        "# --- feature toggles (0 = off) ---",
        f"INTERNSHELPER_FEATURE_COLLECT={flag(collect)}",
        f"INTERNSHELPER_FEATURE_EMAIL={flag(email)}",
        f"INTERNSHELPER_FEATURE_AVAILABILITY={flag(availability)}",
        f"INTERNSHELPER_FEATURE_SCHEDULE={flag(schedule)}",
        f"INTERNSHELPER_FEATURE_DASHBOARD={flag(dashboard)}",
        f"INTERNSHELPER_FEATURE_APP={flag(app)}",
        "",
    ])


def _write_private_file(path: Path, data: bytes) -> bool:
    """Create one owner-only file without following or replacing an existing path."""

    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    except BaseException:
        # Do not leave a partial secret/config file after a failed write.
        path.unlink(missing_ok=True)
        raise
    return True


def scaffold_user_configs(repo_dir: str | Path) -> tuple[Path, ...]:
    """Seed missing private config files from public examples, without overwriting."""

    config_dir = Path(repo_dir) / "config"
    pending = [
        (config_dir / example, config_dir / runtime)
        for example, runtime in USER_CONFIG_TEMPLATES
        if not (config_dir / runtime).exists()
    ]
    missing = [example for example, _runtime in pending if not example.is_file()]
    if missing:
        raise FileNotFoundError(f"public config example not found: {missing[0]}")

    created = []
    for example, runtime in pending:
        if _write_private_file(runtime, example.read_bytes()):
            created.append(runtime)
    return tuple(created)


def scaffold_env(path: str | Path, sender: str = "", recipient: str = "", password: str = "", *,
                 collect: bool = True, email: bool = False, schedule: bool = False,
                 dashboard: bool = True, app: bool = True,
                 availability: bool = True) -> bool:
    """Write `.env` only if absent (never clobber existing secrets). Returns whether written."""
    p = Path(path)
    if p.exists():
        return False
    rendered = render_env(
        sender,
        recipient,
        password,
        collect=collect,
        email=email,
        schedule=schedule,
        dashboard=dashboard,
        app=app,
        availability=availability,
    )
    return _write_private_file(p, rendered.encode("utf-8"))


def realize_plist(template: str | Path, dest: str | Path, repo_dir: str | Path) -> None:
    """Substitute per-machine paths in the launchd template and write the plist."""
    text = (
        Path(template)
        .read_text(encoding="utf-8")
        .replace("__REPO_DIR__", xml_escape(str(repo_dir)))
        .replace("__LOG_DIR__", xml_escape(str(launchd_log_dir())))
    )
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")


def _install_schedule(repo_dir: Path) -> None:
    template = repo_dir / "scripts" / "com.internshelper.run.plist.template"
    dest = Path.home() / "Library" / "LaunchAgents" / f"{_LABEL}.plist"
    launchd_log_dir().mkdir(parents=True, exist_ok=True)
    realize_plist(template, dest, repo_dir)
    print(f"wrote launchd plist: {dest}")
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{_LABEL}"], capture_output=True)
    bootstrap = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(dest)], capture_output=True
    )
    if bootstrap.returncode:
        raise RuntimeError(
            f"launchctl bootstrap failed with exit code {bootstrap.returncode}; "
            f"plist remains at {dest}"
        )
    kickstart = subprocess.run(
        ["launchctl", "kickstart", "-k", f"gui/{uid}/{_LABEL}"], capture_output=True
    )
    if kickstart.returncode:
        raise RuntimeError(
            f"launchctl kickstart failed with exit code {kickstart.returncode}; "
            "the schedule is loaded but its first run did not start"
        )
    print("launchd schedule loaded (hourly + on wake).")


def _install_app(repo_dir: Path) -> None:
    from internshelper import appbundle

    appbundle.main(["--install", "--repo-dir", str(repo_dir)])


def _prompt() -> tuple[bool, bool, bool, bool, bool, str, str, str]:
    def ask(q: str, default: bool) -> bool:
        ans = input(f"{q} [{'Y/n' if default else 'y/N'}] ").strip().lower()
        return default if not ans else ans in ("y", "yes")

    email = ask("Enable optional email digests?", False)
    sender = recipient = password = ""
    if email:
        sender = input("  SMTP sender email: ").strip()
        recipient = input("  SMTP recipient email: ").strip()
        password = getpass.getpass("  SMTP app password (input hidden): ").strip()
    availability = ask("Check application availability during collection?", True)
    schedule = ask("Enable optional hourly collection via launchd (macOS)?", False)
    dashboard = ask("Use the web dashboard?", True)
    app = dashboard and ask("Install the Dock app (InternsHELPer.app → ~/Applications)?",
                            sys.platform == "darwin")
    return email, availability, schedule, dashboard, app, sender, recipient, password


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="internshelper.setup")
    parser.add_argument("--no-input", action="store_true",
                        help="non-interactive; read INTERNSHELPER_FEATURE_*/SMTP_* env + defaults")
    parser.add_argument("--repo-dir", default=None)
    args = parser.parse_args(argv)

    repo_dir = Path(args.repo_dir).resolve() if args.repo_dir else _REPO_ROOT
    if not ensure_gitignore_has_env(repo_dir / ".gitignore"):
        print("ABORT: `.env` is not gitignored — refusing to write secrets. "
              "Add `.env` to .gitignore first.")
        return 1

    try:
        created_configs = scaffold_user_configs(repo_dir)
    except FileNotFoundError as exc:
        print(f"ABORT: {exc}")
        return 1
    if created_configs:
        print("seeded private config: " + ", ".join(str(path) for path in created_configs))

    if args.no_input:
        # An unattended public-clone bootstrap must not opt into email, launchd, or a
        # native-app install merely because the corresponding variable is absent.
        collect = feature_enabled("COLLECT")
        email = feature_enabled("EMAIL", default=False)
        availability = feature_enabled("AVAILABILITY")
        schedule = feature_enabled("SCHEDULE", default=False)
        dashboard = feature_enabled("DASHBOARD")
        app = dashboard and feature_enabled("APP", default=False)
        sender = os.environ.get("INTERNSHELPER_SMTP_SENDER", "") if email else ""
        recipient = os.environ.get("INTERNSHELPER_SMTP_RECIPIENT", "") if email else ""
        password = os.environ.get("INTERNSHELPER_SMTP_PASSWORD", "") if email else ""
    else:
        collect = True
        (
            email,
            availability,
            schedule,
            dashboard,
            app,
            sender,
            recipient,
            password,
        ) = _prompt()

    wrote = scaffold_env(
        repo_dir / ".env",
        sender,
        recipient,
        password,
        collect=collect,
        email=email,
        schedule=schedule,
        dashboard=dashboard,
        app=app,
        availability=availability,
    )
    print(f".env {'written' if wrote else 'already exists (left untouched)'}: {repo_dir / '.env'}")

    if schedule and sys.platform == "darwin":
        _install_schedule(repo_dir)
    elif schedule:
        venv_py = repo_dir / ".venv" / "bin" / "python"
        print("schedule requested but launchd is macOS-only — skipping. For cron/systemd, run:")
        print(f"  {venv_py} -m internshelper.run")
        print("  (config + data paths resolve off the repo automatically, so cwd doesn't matter.)")

    if app and sys.platform == "darwin":
        _install_app(repo_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
