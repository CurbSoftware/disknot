"""Root, the privilege banner's pkexec restart, and the worker command. See docs/tdd-plan.md."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chafftafarian.worker_spawn import (
    new_control_dir,
    real_appimage_path,
    relaunch_argv,
    worker_argv,
)

APPIMAGE_NAME = "disknot-0.1.0-x86_64.AppImage"


def _pkexec() -> str:
    return "/usr/bin/pkexec" if Path("/usr/bin/pkexec").is_file() else "pkexec"


def test_versioned_image_beats_the_fuse_mount(tmp_path):
    image = tmp_path / APPIMAGE_NAME
    image.write_bytes(b"fake")
    mount = f"/tmp/.mount_Disknot/{APPIMAGE_NAME}"
    found = real_appimage_path(
        {"APPIMAGE": mount, "ARGV0": str(image), "OWD": str(tmp_path)},
    )
    assert found == str(image.resolve())
    assert "/.mount_" not in found
    assert found.endswith(APPIMAGE_NAME)


def test_relative_argv0_resolves_from_owd(tmp_path):
    image = tmp_path / APPIMAGE_NAME
    image.write_bytes(b"fake")
    found = real_appimage_path({"ARGV0": APPIMAGE_NAME, "OWD": str(tmp_path)})
    assert found == str(image.resolve())


def test_mount_only_path_is_rejected(tmp_path):
    found = real_appimage_path(
        {"APPIMAGE": f"/tmp/.mount_disknot/{APPIMAGE_NAME}", "OWD": str(tmp_path)},
    )
    assert found is None


def test_missing_image_file_is_rejected(tmp_path):
    missing = tmp_path / APPIMAGE_NAME
    found = real_appimage_path({"APPIMAGE": str(missing), "OWD": str(tmp_path)})
    assert found is None


def test_relaunch_argv_is_pkexec_and_extract_and_run(tmp_path):
    image = tmp_path / APPIMAGE_NAME
    image.write_bytes(b"fake")
    argv = relaunch_argv({"APPIMAGE": str(image), "OWD": str(tmp_path)})
    assert argv[0] == _pkexec()
    assert argv[1:3] == ["/bin/sh", "-c"]
    script = argv[-1]
    assert "APPIMAGE_EXTRACT_AND_RUN=1" in script
    assert APPIMAGE_NAME in script
    assert "/.mount_" not in script
    assert "-m" not in argv
    assert "setsid" in script


def test_relaunch_argv_falls_back_to_the_module(monkeypatch):
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.delenv("ARGV0", raising=False)
    argv = relaunch_argv({})
    assert argv[0] == _pkexec()
    assert "-m" in argv
    assert "chafftafarian" in argv
    assert "APPIMAGE_EXTRACT_AND_RUN=1" not in argv


def test_frozen_binary_outside_the_mount(tmp_path, monkeypatch):
    exe = tmp_path / "disknot"
    exe.write_bytes(b"x")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    argv = relaunch_argv({})
    assert argv == [_pkexec(), str(exe)]


def test_frozen_binary_inside_the_mount_is_not_used(tmp_path, monkeypatch):
    mount = Path("/tmp/.mount_disknot")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(mount / "disknot"))
    argv = relaunch_argv({})
    assert "-m" in argv
    assert "chafftafarian" in argv


def test_worker_argv_is_pkexec(tmp_path, monkeypatch):
    monkeypatch.delenv("APPIMAGE", raising=False)
    argv = worker_argv(tmp_path / "wipe.json")
    assert argv[0] == "pkexec"
    assert "--wipe-worker" in argv
    assert "--config" in argv
    assert "APPIMAGE_EXTRACT_AND_RUN=1" not in argv


def test_worker_argv_sets_extract_and_run_inside_an_appimage(tmp_path, monkeypatch):
    monkeypatch.setenv("APPIMAGE", str(tmp_path / APPIMAGE_NAME))
    argv = worker_argv(tmp_path / "wipe.json")
    assert argv[0] == "pkexec"
    assert "APPIMAGE_EXTRACT_AND_RUN=1" in argv
    assert "--wipe-worker" in argv


def test_control_dir_is_private():
    path = new_control_dir()
    try:
        mode = path.stat().st_mode & 0o777
        assert mode == 0o700
    finally:
        path.rmdir()


def _pyside_unavailable() -> str | None:
    try:
        import PySide6.QtCore  # noqa: F401
    except Exception as exc:
        return f"PySide6 cannot be imported: {exc!r}"
    return None


@pytest.fixture()
def api(tmp_path, monkeypatch):
    reason = _pyside_unavailable()
    if reason:
        pytest.skip(reason)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.instance() or QCoreApplication([])
    from chafftafarian.api import Api

    return Api()


def test_bootstrap_reports_root(api):
    boot = json.loads(api.getBootstrap())
    assert boot["root"] is (os.geteuid() == 0)
    assert "pkexec" in boot["tools"]
    assert "lsblk" in boot["tools"]


def test_relaunch_when_already_root(api, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)

    def explode(*args, **kwargs):
        raise AssertionError("already root must not spawn pkexec")

    monkeypatch.setattr("chafftafarian.api.subprocess.Popen", explode)
    out = json.loads(api.relaunchAsRoot())
    assert out == {"ok": True, "error": None, "already_root": True}


def test_relaunch_failure_stays_on_screen(api, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.setattr(
        "chafftafarian.api.relaunch_argv",
        lambda: ["/bin/sh", "-c", "echo polkit dismissed >&2; exit 126"],
    )
    out = json.loads(api.relaunchAsRoot())
    assert out["ok"] is False
    assert "polkit dismissed" in out["error"]


def test_relaunch_empty_pkexec_failure(api, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.setattr(
        "chafftafarian.api.relaunch_argv",
        lambda: ["/bin/sh", "-c", "exit 1"],
    )
    out = json.loads(api.relaunchAsRoot())
    assert out["ok"] is False
    assert out["error"] == "pkexec exited 1 with no message"


def test_relaunch_oserror(api, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)

    def boom(*args, **kwargs):
        raise OSError("pkexec missing")

    monkeypatch.setattr("chafftafarian.api.subprocess.Popen", boom)
    out = json.loads(api.relaunchAsRoot())
    assert out["ok"] is False
    assert "pkexec missing" in out["error"]


def test_relaunch_success_does_not_require_a_window(api, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.setattr("chafftafarian.api.relaunch_argv", lambda: ["/bin/true"])
    monkeypatch.setattr("PySide6.QtCore.QTimer.singleShot", lambda *args, **kwargs: None)
    out = json.loads(api.relaunchAsRoot())
    assert out["ok"] is True
    assert out["error"] is None
    assert "already_root" not in out
