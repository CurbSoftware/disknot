"""Headless UI for drives, chaff cards, sanitize, and the privilege banner.

Node hosts the page. Pytest owns the assertions. No second test framework,
no display, no pkexec. See docs/tdd-plan.md.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent / "frontend_harness.mjs"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

NVME = {
    "kname": "nvme1n1",
    "path": "/dev/nvme1n1",
    "model": "WD SN770",
    "transport": "nvme",
    "size_bytes": 1000204886016,
    "serial": "SER1",
    "is_nvme": True,
    "os_disk": False,
    "swap": False,
    "mounted": False,
    "partitions": [{"name": "nvme1n1p1", "fstype": "ext4", "mountpoints": [], "size_bytes": 10}],
    "sanitize": {
        "supported": True,
        "block_erase": True,
        "crypto_erase": True,
        "overwrite": False,
    },
}

OS_DISK = {
    **NVME,
    "kname": "nvme0n1",
    "path": "/dev/nvme0n1",
    "model": "OS Drive",
    "os_disk": True,
    "mounted": True,
    "serial": "OS",
}

PLAN = {
    "device": "/dev/nvme1n1",
    "model": "WD SN770",
    "size_bytes": 1000204886016,
    "passes": [
        {
            "n": 1,
            "kind": "sanitize",
            "detail": "block erase",
            "est_s": None,
            "action": "block-erase",
        },
        {"n": 2, "kind": "zeros", "detail": "overwrite with zeros", "est_s": 60, "action": None},
    ],
    "sanitize_supported": True,
    "blockers": [],
    "notes": [],
    "confirmation": {"word": "DESTROY", "device": "/dev/nvme1n1"},
}

DETAIL = {
    **NVME,
    "last_sanitize": {"state": "success", "media_modified": True, "percent": 100},
    "smart": {"passed": True, "temperature_c": 36, "firmware": "731030WD"},
    "smart_error": None,
}


def ui(scenario: str, fixture: dict | None = None) -> dict:
    env = os.environ.copy()
    env["DISKNOT_UI_SCENARIO"] = scenario
    env["DISKNOT_UI_FIXTURE"] = json.dumps(fixture or {})
    proc = subprocess.run(
        [NODE, str(HARNESS)],
        cwd=HARNESS.parent,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


def test_copy_strings():
    got = ui("copy")
    assert "Attached disks" in got["drivesLede"]
    assert "does not wipe a disk" in got["chaffLede"]
    assert "destroys a whole disk" in got["sanitizeLede"]
    assert got["driveIs"].startswith("This drive is /dev/nvme1n1")
    assert "1.0 TiB" in got["driveIs"]
    assert "running OS" in got["roleOs"]
    assert "active swap" in got["roleSwap"]
    assert "mounted" in got["roleMounted"]
    assert "not the OS disk" in got["roleFree"]
    assert "block erase" in got["sanitize"]
    assert "could not be probed" in got["sanitizeBlind"]
    assert "not NVMe" in got["notNvme"]
    assert got["modes"]["exact"].startswith("This mode writes")
    assert "Job" not in got["modes"]["exact"]
    assert "reproducible" in got["seed"]["headline"]
    assert "free space that must remain" in got["reserve"]["headline"]
    assert got["disclaimer"]["headline"] == "Chaff is not sanitization"
    assert "six passes" in got["plans"]["paranoid"]
    assert "DESTROY" in got["arm"]
    assert "/dev/nvme1n1" in got["arm"]
    assert "left zeroed" in got["afterZero"]
    assert "problems" in got["afterFail"]
    assert "mounted at /mnt/data" in got["partitionMounted"]
    assert "not mounted" in got["partitionIdle"]
    for key in ("realistic-desktop", "office-workstation", "storage-test"):
        assert key in got["profiles"]
    assert set(got["layouts"]) == {"realistic", "simple", "flat"}
    assert set(got["completions"]) == {"keep", "delete", "trash"}


def test_drives_render_disks():
    got = ui("drives", {"deviceList": {"devices": [NVME], "error": None}})
    assert "WD SN770" in got["list"]
    assert "/dev/nvme1n1" in got["list"]
    assert "UNMOUNTED" in got["list"]
    assert "1 DISK" in got["chip"]
    assert got["refreshDisabled"] == "false"
    assert "deviceList" in got["calls"]


def test_drives_os_disk_has_no_sanitize_button():
    got = ui("drives", {"deviceList": {"devices": [OS_DISK, NVME], "error": None}})
    assert "OS DISK" in got["list"]
    assert "2 DISKS" in got["chip"]
    assert got["list"].count("Sanitize this drive") == 1
    assert "MOUNTED" in got["list"]


def test_drives_lsblk_error():
    got = ui(
        "drives",
        {"deviceList": {"devices": [], "error": "lsblk: permission denied"}},
    )
    assert "Could not list disks" in got["list"]
    assert "lsblk: permission denied" in got["list"]
    assert "NO DISKS SEEN" in got["chip"]


def test_drives_empty_scan():
    got = ui("drives", {"deviceList": {"devices": [], "error": None}})
    assert "Could not list disks" in got["list"]
    assert "Disk scan returned nothing." in got["list"]


def test_drives_missing_tools_chip():
    got = ui(
        "drives",
        {
            "deviceList": {"devices": [NVME], "error": None},
            "state": {"toolsMissing": ["pkexec", "nvme"]},
        },
    )
    assert "TOOLS MISSING" in got["chip"]
    assert "PKEXEC" in got["chip"]
    assert "NVME" in got["chip"]


def test_drives_inspect():
    got = ui(
        "drives-inspect",
        {
            "deviceList": {"devices": [NVME], "error": None},
            "deviceDetail": DETAIL,
        },
    )
    assert "SMART PASSED" in got["inspect"]
    assert "LAST: SUCCESS" in got["inspect"]
    assert "731030WD" in got["inspect"]
    assert "deviceDetail" in got["calls"]


def test_chaff_cards_job_preflight_ground_rules():
    got = ui("chaff")
    assert "Job" in got["form"]
    assert "WHAT TO GENERATE AND WHERE" in got["form"]
    assert "Preflight" in got["form"]
    assert "Writes nothing" in got["form"]
    assert "NOT RUN YET" in got["preflight"]
    assert "writes nothing" in got["preflight"].lower()
    assert "Ground rules" in got["edu"]
    assert "This seed makes the corpus reproducible" in got["edu"]
    assert "This reserve is free space that must remain" in got["edu"]
    assert "Chaff is not sanitization" in got["edu"]
    assert "chaffStart" not in got["calls"]


def test_chaff_preflight_refused():
    got = ui(
        "chaff-preflight",
        {"chaffPreflight": {"ok": False, "error": "Permission denied writing to /mnt/ro"}},
    )
    assert "REFUSED" in got["preflight"]
    assert "Permission denied writing to /mnt/ro" in got["preflight"]
    assert "chaffPreflight" in got["calls"]


def test_chaff_preflight_clear():
    got = ui(
        "chaff-preflight",
        {
            "chaffPreflight": {
                "ok": True,
                "target_path": "/srv/chaff",
                "requested_bytes": 1048576,
                "free_bytes": 99999999999,
                "reserve_bytes": 2000000000,
                "estimated_file_count": 12,
                "formats": ["txt", "pdf"],
                "projected_remaining_bytes": 80000000000,
                "warnings": [],
            }
        },
    )
    assert "WHAT THE ENGINE SEES" in got["preflight"]
    assert "CLEAR" in got["preflight"]
    assert "/srv/chaff" in got["preflight"]
    assert "txt" in got["preflight"]


def test_chaff_preflight_notes():
    got = ui(
        "chaff-preflight",
        {
            "chaffPreflight": {
                "ok": True,
                "target_path": "/srv/chaff",
                "requested_bytes": 1048576,
                "free_bytes": 99999999999,
                "reserve_bytes": 2000000000,
                "estimated_file_count": 3,
                "formats": ["txt"],
                "warnings": ["low space"],
            }
        },
    )
    assert "NOTES" in got["preflight"]
    assert "low space" in got["preflight"]


def test_chaff_amount_mode_switches():
    got = ui("chaff-mode")
    assert "Percent of free" in got["form"]
    assert "percent free" in got["form"]


def test_chaff_start_without_target_runs_preflight():
    got = ui(
        "chaff-start",
        {"chaffPreflight": {"ok": False, "error": "Target directory does not exist: "}},
    )
    assert "REFUSED" in got["preflight"]
    assert "does not exist" in got["preflight"]
    assert "chaffStart" not in got["calls"]
    assert "chaffPreflight" in got["calls"]


def test_privilege_banner_when_not_root():
    got = ui(
        "privilege",
        {"getBootstrap": _bootstrap(root=False)},
    )
    assert got["bannerHidden"] is False
    assert "Restart with privileges" in got["banner"]
    assert "Disk listing still runs" in got["banner"]
    assert "Wipe and sanitize need root" in got["banner"]


def test_privilege_banner_hidden_when_root():
    got = ui("privilege", {"getBootstrap": _bootstrap(root=True)})
    assert got["bannerHidden"] is True
    assert got["banner"] == ""


def test_privilege_pkexec_failure_stays_visible():
    got = ui(
        "privilege-click",
        {
            "getBootstrap": _bootstrap(root=False),
            "relaunchAsRoot": {"ok": False, "error": "polkit dismissed"},
        },
    )
    assert got["bannerHidden"] is False
    assert "polkit dismissed" in got["err"]
    assert got["restartDisabled"] is None
    assert "relaunchAsRoot" in got["calls"]


def test_sanitize_plan_card():
    got = ui(
        "sanitize",
        {"deviceList": {"devices": [NVME], "error": None}, "wipePlan": PLAN},
    )
    assert "Target and plan" in got["form"]
    assert "paranoid" in got["form"]
    assert "The plan" in got["plan"]
    assert "SANITIZE AVAILABLE" in got["plan"]
    assert "CLEAR TO CONFIRM" in got["safety"]
    assert "DESTROY" in got["safety"]
    assert "wipePlan" in got["calls"]


def test_sanitize_blocked():
    blocked = {**PLAN, "blockers": ["mounted: /mnt/data: unmount first, or let the worker do it"]}
    got = ui(
        "sanitize",
        {"deviceList": {"devices": [NVME], "error": None}, "wipePlan": blocked},
    )
    assert "BLOCKED" in got["safety"]
    assert "/mnt/data" in got["safety"]


def test_sanitize_lsblk_error():
    got = ui(
        "sanitize",
        {"deviceList": {"devices": [], "error": "lsblk: permission denied"}},
    )
    assert "Could not list disks" in got["root"]
    assert "lsblk: permission denied" in got["root"]
    assert "wipePlan" not in got["calls"]


def test_sanitize_non_root_asks_for_pkexec():
    got = ui(
        "sanitize-start",
        {
            "deviceList": {"devices": [NVME], "error": None},
            "wipePlan": PLAN,
            "relaunchAsRoot": {"ok": False, "error": "pkexec exited 126 with no message"},
        },
    )
    assert "password prompt" in got["log"]
    assert "pkexec exited 126" in got["log"]
    assert "wipeStart" not in got["calls"]
    assert "relaunchAsRoot" in got["calls"]


def _bootstrap(root: bool) -> dict:
    return {
        "core": {"ready": True, "version": "0.1.0"},
        "tools": {"lsblk": "/usr/bin/lsblk", "pkexec": "/usr/bin/pkexec"},
        "busy": False,
        "kind": None,
        "root": root,
        "app_settings": {},
    }
