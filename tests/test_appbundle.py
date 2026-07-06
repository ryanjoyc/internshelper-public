"""Unit tests for the Dock-app bundle builder (internshelper.appbundle).

Covers the pure/scaffolding pieces: launcher rendering, Info.plist contents, the
iconset mapping, bundle structure, and replace-install. The sips/iconutil icon build
and the Dock itself are exercised manually.
"""

import os
import plistlib

from internshelper import appbundle


def test_render_launcher_substitutes_repo_dir():
    out = appbundle.render_launcher("/repo/here")
    assert '"/repo/here/.venv/bin/python" -m internshelper.app' in out
    assert "__" not in out
    assert out.startswith("#!/bin/bash")


def test_render_info_plist_parses_and_matches_executable():
    info = plistlib.loads(appbundle.render_info_plist())
    assert info["CFBundleExecutable"] == appbundle.EXECUTABLE_NAME
    assert info["CFBundleIdentifier"] == "com.internshelper.app"
    assert info["CFBundleIconFile"] == "icon"
    assert info["CFBundlePackageType"] == "APPL"
    assert info["NSHighResolutionCapable"] is True


def test_iconset_entries_mapping():
    entries = dict(appbundle.iconset_entries())
    assert len(entries) == 10
    assert entries["icon_16x16.png"] == 16
    assert entries["icon_16x16@2x.png"] == 32
    assert entries["icon_512x512.png"] == 512
    assert entries["icon_512x512@2x.png"] == 1024


def test_build_bundle_structure(tmp_path):
    bundle = appbundle.build_bundle(tmp_path, "/repo/here", icns=None)
    assert bundle == tmp_path / appbundle.BUNDLE_NAME
    launcher = bundle / "Contents" / "MacOS" / appbundle.EXECUTABLE_NAME
    assert launcher.exists()
    assert os.access(launcher, os.X_OK)
    assert "/repo/here" in launcher.read_text()
    info = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == launcher.name
    assert (bundle / "Contents" / "Resources").is_dir()
    assert not (bundle / "Contents" / "Resources" / "icon.icns").exists()


def test_build_bundle_rebuild_is_clean(tmp_path):
    bundle = appbundle.build_bundle(tmp_path, "/repo/one")
    (bundle / "Contents" / "stray.txt").write_text("leftover")
    bundle = appbundle.build_bundle(tmp_path, "/repo/two")
    assert not (bundle / "Contents" / "stray.txt").exists()
    launcher = bundle / "Contents" / "MacOS" / appbundle.EXECUTABLE_NAME
    assert "/repo/two" in launcher.read_text()


def test_build_bundle_includes_icns_when_given(tmp_path):
    fake_icns = tmp_path / "fake.icns"
    fake_icns.write_bytes(b"icns-bytes")
    bundle = appbundle.build_bundle(tmp_path / "out", "/repo/here", icns=fake_icns)
    assert (bundle / "Contents" / "Resources" / "icon.icns").read_bytes() == b"icns-bytes"


def test_install_bundle_replaces_existing(tmp_path):
    bundle = appbundle.build_bundle(tmp_path / "build", "/repo/new")
    target = tmp_path / "Applications"
    stale = target / appbundle.BUNDLE_NAME
    (stale / "Contents").mkdir(parents=True)
    (stale / "Contents" / "old.txt").write_text("stale")
    dest = appbundle.install_bundle(bundle, target)
    assert dest == stale
    assert not (dest / "Contents" / "old.txt").exists()
    launcher = dest / "Contents" / "MacOS" / appbundle.EXECUTABLE_NAME
    assert "/repo/new" in launcher.read_text()
    assert os.access(launcher, os.X_OK)  # exec bit survives the copy
