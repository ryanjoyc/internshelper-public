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

from internshelper.dotenv import feature_enabled

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LABEL = "com.internshelper.run"
_IGNORE_MATCHES = {".env", "/.env", "*.env"}


def ensure_gitignore_has_env(gitignore_path: str | Path) -> bool:
    """True iff `.env` is ignored by the given .gitignore. Pure check (no write)."""
    p = Path(gitignore_path)
    if not p.exists():
        return False
    return any(line.strip() in _IGNORE_MATCHES for line in p.read_text(encoding="utf-8").splitlines())


def render_env(sender: str = "", recipient: str = "", password: str = "", *,
               email: bool = True, schedule: bool = True, dashboard: bool = True) -> str:
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
        "INTERNSHELPER_FEATURE_COLLECT=1",
        f"INTERNSHELPER_FEATURE_EMAIL={flag(email)}",
        f"INTERNSHELPER_FEATURE_SCHEDULE={flag(schedule)}",
        f"INTERNSHELPER_FEATURE_DASHBOARD={flag(dashboard)}",
        "",
    ])


def scaffold_env(path: str | Path, sender: str = "", recipient: str = "", password: str = "", *,
                 email: bool = True, schedule: bool = True, dashboard: bool = True) -> bool:
    """Write `.env` only if absent (never clobber existing secrets). Returns whether written."""
    p = Path(path)
    if p.exists():
        return False
    p.write_text(render_env(sender, recipient, password,
                            email=email, schedule=schedule, dashboard=dashboard), encoding="utf-8")
    return True


def realize_plist(template: str | Path, dest: str | Path, repo_dir: str | Path) -> None:
    """Substitute __REPO_DIR__ in the launchd template and write the realized plist."""
    text = Path(template).read_text(encoding="utf-8").replace("__REPO_DIR__", str(repo_dir))
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")


def _install_schedule(repo_dir: Path) -> None:
    template = repo_dir / "scripts" / "com.internshelper.run.plist.template"
    dest = Path.home() / "Library" / "LaunchAgents" / f"{_LABEL}.plist"
    realize_plist(template, dest, repo_dir)
    print(f"wrote launchd plist: {dest}")
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{_LABEL}"], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(dest)], capture_output=True)
    subprocess.run(["launchctl", "kickstart", "-k", f"gui/{uid}/{_LABEL}"], capture_output=True)
    print("launchd schedule loaded (hourly + on wake).")


def _prompt() -> tuple[bool, bool, bool, str, str, str]:
    def ask(q: str, default: bool) -> bool:
        ans = input(f"{q} [{'Y/n' if default else 'y/N'}] ").strip().lower()
        return default if not ans else ans in ("y", "yes")

    email = ask("Enable email nudges?", True)
    sender = recipient = password = ""
    if email:
        sender = input("  SMTP sender email: ").strip()
        recipient = input("  SMTP recipient email: ").strip()
        password = getpass.getpass("  SMTP app password (input hidden): ").strip()
    schedule = ask("Schedule the hourly collector via launchd (macOS)?", sys.platform == "darwin")
    dashboard = ask("Use the Streamlit dashboard?", True)
    return email, schedule, dashboard, sender, recipient, password


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

    if args.no_input:
        email = feature_enabled("EMAIL")
        schedule = feature_enabled("SCHEDULE")
        dashboard = feature_enabled("DASHBOARD")
        sender = os.environ.get("INTERNSHELPER_SMTP_SENDER", "") if email else ""
        recipient = os.environ.get("INTERNSHELPER_SMTP_RECIPIENT", "") if email else ""
        password = os.environ.get("INTERNSHELPER_SMTP_PASSWORD", "") if email else ""
    else:
        email, schedule, dashboard, sender, recipient, password = _prompt()

    wrote = scaffold_env(repo_dir / ".env", sender, recipient, password,
                         email=email, schedule=schedule, dashboard=dashboard)
    print(f".env {'written' if wrote else 'already exists (left untouched)'}: {repo_dir / '.env'}")

    if schedule and sys.platform == "darwin":
        _install_schedule(repo_dir)
    elif schedule:
        venv_py = repo_dir / ".venv" / "bin" / "python"
        print("schedule requested but launchd is macOS-only — skipping. For cron/systemd, run:")
        print(f"  {venv_py} -m internshelper.run")
        print("  (config + data paths resolve off the repo automatically, so cwd doesn't matter.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
