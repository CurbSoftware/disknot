"""Drive detection. See docs/tdd-plan.md."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chafftafarian.wipe import inventory
from chafftafarian.wipe.tools import FakeToolRunner, SubprocessToolRunner
from suite_fakes import disk_node, lsblk_json, part_node, scripted_runner


def test_scan_lists_whole_disks_and_rewrites_dev_dir(tmp_path):
    runner = scripted_runner([disk_node(), disk_node("sda", tran="sata", model="Samsung")])
    scan = inventory.scan_devices(runner, dev_dir=str(tmp_path))
    assert scan.error is None
    assert [d.kname for d in scan.devices] == ["nvme1n1", "sda"]
    assert scan.devices[0].path == f"{tmp_path}/nvme1n1"
    assert scan.devices[0].is_nvme and scan.devices[0].is_disk
    assert scan.devices[1].is_nvme is False
    assert scan.devices[0].model == "WD SN770"
    assert scan.devices[0].size_bytes == 1000


def test_scan_keeps_partitions_mounts_and_swap():
    node = disk_node(
        "sda",
        tran="sata",
        children=[
            part_node("sda1", mountpoints=[["/mnt/a", "/mnt/a/sub"]], fstype="btrfs"),
            part_node("sda2", mountpoints=["[SWAP]"], fstype="swap"),
        ],
    )
    dev = inventory.scan_devices(scripted_runner([node])).devices[0]
    assert dev.mounted and dev.swap
    assert dev.mountpoints() == ["/mnt/a", "/mnt/a/sub"]
    assert dev.partitions[0].fstype == "btrfs"
    assert "[SWAP]" not in dev.mountpoints()


def test_whole_disk_swap_without_partitions():
    node = disk_node(mountpoints=["[SWAP]"])
    dev = inventory.scan_devices(scripted_runner([node])).devices[0]
    assert dev.swap
    assert dev.mountpoints() == []


def test_scan_drops_zram_ram_and_optical():
    nodes = [
        disk_node(),
        disk_node("zram0", model=None),
        disk_node("ram0", model=None),
        {
            "name": "sr0",
            "kname": "sr0",
            "type": "rom",
            "size": 1,
            "model": None,
            "tran": "sata",
            "mountpoints": [None],
            "children": [],
        },
        {
            "name": "nvme1n1p1",
            "kname": "nvme1n1p1",
            "type": "part",
            "size": 10,
            "mountpoints": ["/"],
            "children": [],
        },
    ]
    scan = inventory.scan_devices(scripted_runner(nodes))
    assert [d.kname for d in scan.devices] == ["nvme1n1"]
    assert scan.error is None


def test_scan_skips_non_dicts_and_bad_children():
    node = disk_node(children=["nope", {"name": "p1", "size": "lots", "mountpoints": None}])
    dev = inventory.scan_devices(scripted_runner([node])).devices[0]
    assert len(dev.partitions) == 1
    assert dev.partitions[0].size_bytes == 0
    assert dev.partitions[0].mountpoints == []


def test_scan_treats_missing_size_as_zero():
    node = disk_node()
    node["size"] = None
    dev = inventory.scan_devices(scripted_runner([node])).devices[0]
    assert dev.size_bytes == 0


@pytest.mark.parametrize(
    ("stderr", "rc", "expected"),
    [
        ("lsblk: permission denied", 1, "permission denied"),
        ("lsblk: command not found", 127, "command not found"),
        ("", 2, "lsblk exited 2"),
    ],
)
def test_lsblk_error_text_is_kept(stderr, rc, expected):
    runner = scripted_runner(lsblk_rc=rc, lsblk_stderr=stderr)
    scan = inventory.scan_devices(runner)
    assert scan.devices == []
    assert expected in (scan.error or "")


def test_lsblk_non_json():
    scan = inventory.scan_devices(scripted_runner(lsblk_stdout="not json"))
    assert scan.devices == []
    assert scan.error == "lsblk did not return JSON."


def test_lsblk_without_blockdevices():
    scan = inventory.scan_devices(scripted_runner(lsblk_stdout="{}"))
    assert scan.error == "lsblk JSON had no blockdevices list."
    scan = inventory.scan_devices(scripted_runner(lsblk_stdout="[]"))
    assert scan.error == "lsblk JSON had no blockdevices list."


def test_lsblk_blockdevices_not_a_list():
    scan = inventory.scan_devices(scripted_runner(lsblk_stdout=json.dumps({"blockdevices": {}})))
    assert scan.error == "lsblk JSON had no blockdevices list."


def test_lsblk_with_no_whole_disks():
    scan = inventory.scan_devices(scripted_runner([]))
    assert scan.devices == []
    assert scan.error == "lsblk reported no whole disks."


def test_list_devices_hides_the_error_string():
    assert inventory.list_devices(scripted_runner(lsblk_rc=1, lsblk_stderr="lsblk: boom")) == []


def test_lsblk_argv_does_not_ask_for_children_column():
    joined = ",".join(inventory._LSBLK_ARGS)
    assert "CHILDREN" not in joined
    assert "MOUNTPOINTS" in joined
    assert "FSTYPE" in joined
    runner = scripted_runner([disk_node()])
    inventory.scan_devices(runner)
    assert runner.calls[0][:3] == ["lsblk", "-Jb", "-o"]


def test_enrich_fills_blank_model_and_leaves_lsblk_model():
    blank = disk_node(model=None)
    blank["serial"] = None
    named = disk_node("sda", tran="sata", model="Keep me")
    runner = FakeToolRunner(
        lambda argv: FakeToolRunner.ok(
            '{"Devices":[{"DevicePath":"/dev/nvme1n1","ModelNumber":"WD_BLACK",'
            '"SerialNumber":"WD-123"}]}'
        )
    )
    devices = inventory.enrich_with_nvme(
        inventory.scan_devices(scripted_runner([blank, named])).devices,
        runner,
    )
    by_name = {d.kname: d for d in devices}
    assert by_name["nvme1n1"].model == "WD_BLACK"
    assert by_name["nvme1n1"].serial == "WD-123"
    assert by_name["sda"].model == "Keep me"


def test_enrich_ignores_failed_nvme_list():
    devices = inventory.list_devices(scripted_runner([disk_node(model=None)]))
    runner = FakeToolRunner(FakeToolRunner.err("nvme: command not found", rc=127))
    again = inventory.enrich_with_nvme(devices, runner)
    assert again[0].model is None


def test_missing_binary_is_a_tool_error():
    result = SubprocessToolRunner().run(["disknot-missing-binary-xyz"], timeout=5)
    assert result.ok is False
    assert result.rc == 127
    assert "command not found" in result.stderr


def test_tool_timeout_is_reported():
    result = SubprocessToolRunner().run(["sleep", "5"], timeout=0.2)
    assert result.ok is False
    assert result.rc == 124
    assert "timed out" in result.stderr


def test_lsblk_json_round_trip_shape():
    raw = lsblk_json([disk_node()])
    assert json.loads(raw)["blockdevices"][0]["kname"] == "nvme1n1"
