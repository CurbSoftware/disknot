"""Api slots for drives, wipe plans, and start refusals. See docs/tdd-plan.md.

The runner is fake. These tests never open a block device and never call pkexec.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from suite_fakes import disk_node, part_node, scripted_runner


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


def test_device_list_returns_disks(api):
    api._runner = scripted_runner([disk_node(), disk_node("sda", tran="sata", model="Samsung")])
    out = json.loads(api.deviceList())
    assert out["error"] is None
    by_name = {dev["kname"]: dev for dev in out["devices"]}
    assert by_name["nvme1n1"]["path"] == "/dev/nvme1n1"
    assert by_name["nvme1n1"]["is_nvme"] is True
    assert by_name["nvme1n1"]["sanitize"]["block_erase"] is True
    assert by_name["nvme1n1"]["sanitize"]["crypto_erase"] is True
    assert by_name["sda"]["is_nvme"] is False
    assert by_name["sda"]["sanitize"] is None
    assert by_name["nvme1n1"]["os_disk"] is False


def test_device_list_surfaces_lsblk_errors(api):
    api._runner = scripted_runner(lsblk_rc=1, lsblk_stderr="lsblk: permission denied")
    out = json.loads(api.deviceList())
    assert out["devices"] == []
    assert "permission denied" in out["error"]


def test_device_list_surfaces_bad_json(api):
    api._runner = scripted_runner(lsblk_stdout="this is not json")
    out = json.loads(api.deviceList())
    assert out["devices"] == []
    assert out["error"] == "lsblk did not return JSON."


def test_device_list_when_lsblk_exits_quietly(api):
    api._runner = scripted_runner(lsblk_rc=2, lsblk_stderr="")
    out = json.loads(api.deviceList())
    assert out["devices"] == []
    assert out["error"] == "lsblk exited 2"


def test_device_list_when_lsblk_reports_no_disks(api):
    api._runner = scripted_runner([])
    out = json.loads(api.deviceList())
    assert out["devices"] == []
    assert out["error"] == "lsblk reported no whole disks."


def test_device_detail_missing(api):
    api._runner = scripted_runner([disk_node()])
    out = json.loads(api.deviceDetail("/dev/sdz"))
    assert "no device" in out["error"]


def test_device_detail_includes_sanitize_log(api):
    api._runner = scripted_runner([disk_node()])
    out = json.loads(api.deviceDetail("/dev/nvme1n1"))
    assert out["kname"] == "nvme1n1"
    assert out["last_sanitize"]["state"] == "success"
    assert out["last_sanitize"]["media_modified"] is True


def test_wipe_plan_for_a_listed_disk(api):
    api._runner = scripted_runner([disk_node(size=2_000_000_000)])
    out = json.loads(api.wipePlan(json.dumps({"device": "/dev/nvme1n1", "plan": "quick"})))
    assert "error" not in out
    assert [item["kind"] for item in out["passes"]] == ["sanitize", "zeros"]
    assert out["confirmation"] == {"word": "DESTROY", "device": "/dev/nvme1n1"}
    assert out["blockers"] == []


def test_wipe_plan_unknown_device(api):
    api._runner = scripted_runner([disk_node()])
    out = json.loads(api.wipePlan(json.dumps({"device": "/dev/sdz"})))
    assert out["error"] == "no device /dev/sdz"


def test_wipe_plan_lists_mount_blockers(api):
    node = disk_node(children=[part_node("nvme1n1p1", mountpoints=["/mnt/data"])])
    api._runner = scripted_runner([node])
    out = json.loads(
        api.wipePlan(json.dumps({"device": "/dev/nvme1n1", "plan": "standard", "unmount": False}))
    )
    assert any("mounted" in item for item in out["blockers"])
    assert [item["kind"] for item in out["passes"]] == ["sanitize", "zeros", "ones"]


def test_wipe_start_refuses_confirmation_mismatch(api):
    api._runner = scripted_runner([disk_node()])
    out = json.loads(
        api.wipeStart(
            json.dumps(
                {
                    "device": "/dev/nvme1n1",
                    "plan": "quick",
                    "confirm_word": "nope",
                    "confirm_device": "/dev/nvme1n1",
                }
            )
        )
    )
    assert out["ok"] is False
    assert "confirmation" in out["error"]
    assert api._wipe_proc is None


def test_wipe_start_refuses_blockers_without_spawning(api):
    node = disk_node(children=[part_node("nvme1n1p1", mountpoints=["/mnt/data"])])
    api._runner = scripted_runner([node])
    out = json.loads(
        api.wipeStart(
            json.dumps(
                {
                    "device": "/dev/nvme1n1",
                    "plan": "quick",
                    "confirm_word": "DESTROY",
                    "confirm_device": "/dev/nvme1n1",
                }
            )
        )
    )
    assert out["ok"] is False
    assert "mounted" in out["error"]
    assert api._wipe_proc is None


def test_wipe_start_unknown_device(api):
    api._runner = scripted_runner([disk_node()])
    out = json.loads(api.wipeStart(json.dumps({"device": "/dev/sdz", "confirm_word": "DESTROY"})))
    assert out["ok"] is False
    assert "no device" in out["error"]
    assert api._wipe_proc is None
