"""Shared lsblk fixtures for the Disknot suite. Not collected by pytest."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chafftafarian.wipe.tools import FakeToolRunner, ToolResult


def disk_node(
    kname: str = "nvme1n1",
    *,
    size: int = 1000,
    model: str = "WD SN770",
    tran: str = "nvme",
    children: list | None = None,
    mountpoints: list | None = None,
) -> dict:
    return {
        "name": kname,
        "kname": kname,
        "type": "disk",
        "size": size,
        "model": model,
        "serial": "SER1",
        "tran": tran,
        "mountpoints": [None] if mountpoints is None else mountpoints,
        "fstype": None,
        "children": [] if children is None else children,
    }


def part_node(
    name: str,
    *,
    size: int = 100,
    mountpoints: list | None = None,
    fstype: str | None = "ext4",
) -> dict:
    return {
        "name": name,
        "kname": name,
        "type": "part",
        "size": size,
        "mountpoints": [] if mountpoints is None else mountpoints,
        "fstype": fstype,
        "children": [],
    }


def lsblk_json(nodes: list[dict]) -> str:
    return json.dumps({"blockdevices": nodes})


class ScriptedRunner:
    """FakeToolRunner pops a callable after one call. This one keeps answering."""

    def __init__(self, respond) -> None:
        self._respond = respond
        self.calls: list[list[str]] = []

    def run(self, argv: list[str], timeout: float | None = None) -> ToolResult:
        self.calls.append(list(argv))
        return self._respond(list(argv))


def scripted_runner(
    nodes: list[dict] | None = None,
    *,
    lsblk_rc: int = 0,
    lsblk_stdout: str | None = None,
    lsblk_stderr: str = "",
    id_ctrl: str = "sanicap : 0x7\n",
    sanitize_log: str = "sprog : 65535\nsstat : 0x101\n",
) -> ScriptedRunner:
    """Answer lsblk, findmnt, and nvme without touching a real disk."""
    payload = lsblk_json(nodes or []) if lsblk_stdout is None else lsblk_stdout

    def respond(argv: list[str]) -> ToolResult:
        prog = argv[0]
        if prog == "lsblk":
            return ToolResult(lsblk_rc, payload if lsblk_rc == 0 else "", lsblk_stderr)
        if prog == "findmnt":
            return FakeToolRunner.ok("")
        if prog == "nvme" and "id-ctrl" in argv:
            return FakeToolRunner.ok(id_ctrl)
        if prog == "nvme" and "sanitize-log" in argv:
            return FakeToolRunner.ok(sanitize_log)
        if prog == "nvme":
            return FakeToolRunner.ok('{"Devices":[]}')
        return FakeToolRunner.ok("")

    return ScriptedRunner(respond)
