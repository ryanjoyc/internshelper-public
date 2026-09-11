"""Build + install the InternsHELPer.app Dock bundle (macOS, stdlib only).

    python -m internshelper.appbundle             # build into <repo>/build/
    python -m internshelper.appbundle --install   # ... and copy to ~/Applications

The bundle is a thin wrapper: its launcher execs `<repo>/.venv/bin/python -m
internshelper.app`, so the repo dir is baked in at build time (same limitation as the
launchd plist — moving the repo means re-running `--install`). The .icns is built from
the committed 1024px PNG with stock `sips` + `iconutil`; if that fails the bundle is
still produced, just without an icon.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from internshelper import __version__

_REPO_ROOT = Path(__file__).resolve().parent.parent
BUNDLE_NAME = "InternsHELPer.app"
EXECUTABLE_NAME = "internshelper"
ICON_PNG = _REPO_ROOT / "internshelper" / "assets" / "icon-1024.png"

def render_launcher(repo_dir: str | Path) -> str:
    """Render a launcher whose baked-in paths are safe shell arguments."""
    repo_dir = Path(repo_dir)
    quoted_repo = shlex.quote(str(repo_dir))
    quoted_python = shlex.quote(str(repo_dir / ".venv" / "bin" / "python"))
    return (
        "#!/bin/bash\n"
        f"cd {quoted_repo} || exit 1\n"
        f"exec {quoted_python} -m internshelper.app\n"
    )


def render_info_plist() -> bytes:
    return plistlib.dumps({
        "CFBundleName": "InternsHELPer",
        "CFBundleDisplayName": "InternsHELPer",
        "CFBundleIdentifier": "com.internshelper.app",
        "CFBundleExecutable": EXECUTABLE_NAME,
        "CFBundleIconFile": "icon",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
    })


def iconset_entries() -> list[tuple[str, int]]:
    """The filenames + pixel sizes iconutil expects in an .iconset directory."""
    entries = []
    for base in (16, 32, 128, 256, 512):
        entries.append((f"icon_{base}x{base}.png", base))
        entries.append((f"icon_{base}x{base}@2x.png", base * 2))
    return entries


def build_icns(png_1024: str | Path, dest_icns: str | Path, work_dir: str | Path) -> bool:
    """Render the .icns from a 1024px PNG via sips + iconutil. False (never raise) on failure."""
    png_1024 = Path(png_1024)
    if not png_1024.exists():
        print(f"warning: icon source missing ({png_1024}); building without an icon")
        return False
    iconset = Path(work_dir) / "icon.iconset"
    shutil.rmtree(iconset, ignore_errors=True)
    iconset.mkdir(parents=True)
    try:
        for name, px in iconset_entries():
            subprocess.run(
                ["sips", "-z", str(px), str(px), str(png_1024),
                 "--out", str(iconset / name)],
                check=True, capture_output=True)
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(dest_icns)],
            check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"warning: icon build failed ({e}); building without an icon")
        return False
    return True


def build_bundle(dest_dir: str | Path, repo_dir: str | Path, *, icns: str | Path | None = None) -> Path:
    """Scaffold the .app under dest_dir (wipe + recreate; idempotent)."""
    bundle = Path(dest_dir) / BUNDLE_NAME
    shutil.rmtree(bundle, ignore_errors=True)
    contents = bundle / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()

    (contents / "Info.plist").write_bytes(render_info_plist())
    launcher = contents / "MacOS" / EXECUTABLE_NAME
    launcher.write_text(render_launcher(repo_dir), encoding="utf-8")
    launcher.chmod(0o755)
    if icns is not None and Path(icns).exists():
        shutil.copyfile(icns, contents / "Resources" / "icon.icns")
    return bundle


def install_bundle(bundle: str | Path, target_dir: str | Path | None = None) -> Path:
    """Replace-copy the bundle into ~/Applications (no sudo)."""
    target_dir = Path(target_dir) if target_dir else Path.home() / "Applications"
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / Path(bundle).name
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(bundle, dest)
    os.utime(dest)  # nudge the LaunchServices/Dock icon cache
    return dest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="internshelper.appbundle")
    parser.add_argument("--install", action="store_true",
                        help="also copy the bundle to the target dir (default ~/Applications)")
    parser.add_argument("--repo-dir", default=None)
    parser.add_argument("--target-dir", default=None)
    args = parser.parse_args(argv)

    if sys.platform != "darwin":
        print("The Dock app bundle is macOS-only — skipping.")
        return 0

    repo_dir = Path(args.repo_dir).resolve() if args.repo_dir else _REPO_ROOT
    build_dir = repo_dir / "build"
    build_dir.mkdir(parents=True, exist_ok=True)

    icns = build_dir / "icon.icns"
    if not build_icns(ICON_PNG, icns, build_dir):
        icns = None
    bundle = build_bundle(build_dir, repo_dir, icns=icns)
    print(f"built: {bundle}")

    if args.install:
        dest = install_bundle(bundle, args.target_dir)
        print(f"installed: {dest}")
        print("Drag it from there onto the Dock (Finder > Go > Applications in your home folder).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
