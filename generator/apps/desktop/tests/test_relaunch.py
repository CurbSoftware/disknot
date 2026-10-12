"""Privilege restart must target the real AppImage, not the FUSE mount."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chafftafarian.worker_spawn import real_appimage_path, relaunch_argv


def test_mount_path_is_rejected(tmp_path):
    image = tmp_path / "disknot-0.1.0-x86_64.AppImage"
    image.write_bytes(b"fake")
    mount = "/tmp/.mount_Disknot/disknot-0.1.0-x86_64.AppImage"
    found = real_appimage_path(
        {"APPIMAGE": mount, "ARGV0": str(image), "OWD": str(tmp_path)},
    )
    assert found == str(image.resolve())
    assert "/.mount_" not in found


def test_relaunch_argv_uses_the_real_image(tmp_path):
    image = tmp_path / "disknot-0.1.0-x86_64.AppImage"
    image.write_bytes(b"fake")
    argv = relaunch_argv({"APPIMAGE": str(image), "OWD": str(tmp_path)})
    assert argv[0].endswith("pkexec")
    assert "/bin/sh" in argv
    script = argv[-1]
    assert "APPIMAGE_EXTRACT_AND_RUN=1" in script
    assert str(image) in script
    assert "/.mount_" not in script
    assert "-m" not in argv
