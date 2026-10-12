"""Wipe plans, safety, and the root worker gate. See docs/tdd-plan.md."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chafftafarian.wipe import inventory, plan, protocol, safety, worker
from chafftafarian.wipe.ata import AtaCaps
from chafftafarian.wipe.nvme import SanitizeCaps
from chafftafarian.wipe.tools import FakeToolRunner


def make_dev(**kwargs) -> inventory.DeviceInfo:
    base = dict(
        kname="nvme1n1",
        path="/dev/nvme1n1",
        type="disk",
        size_bytes=2_000_000_000,
        model="WD SN770",
        serial="SER1",
        transport="nvme",
    )
    base.update(kwargs)
    return inventory.DeviceInfo(**base)


def os_runner(root_source: str = "/dev/nvme0n1p2") -> FakeToolRunner:
    def respond(argv: list[str]):
        if argv and argv[0] == "findmnt" and argv[-1] in safety._CRITICAL_MOUNTS:
            return FakeToolRunner.ok(f"{root_source}\n")
        return FakeToolRunner.ok("")

    return FakeToolRunner(respond)


def caps(raw: int = 0b111) -> SanitizeCaps:
    return SanitizeCaps(raw)


@pytest.mark.parametrize(
    ("name", "kinds"),
    [
        ("paranoid", ["sanitize", "zeros", "ones", "zeros", "sanitize", "zeros"]),
        ("standard", ["sanitize", "zeros", "ones"]),
        ("quick", ["sanitize", "zeros"]),
    ],
)
def test_plan_shapes(name, kinds):
    built = plan.build_plan(make_dev(), caps(), plan=name)
    assert [p.kind for p in built.passes] == kinds
    assert built.blockers == []
    assert built.sanitize_supported is True
    assert all(p.detail for p in built.passes)


def test_verify_is_a_trailing_pass():
    built = plan.build_plan(make_dev(), caps(), plan="quick", verify=True)
    assert [p.kind for p in built.passes] == ["sanitize", "zeros", "verify"]
    assert "sample" in built.passes[-1].detail


def test_missing_sanitize_drops_those_passes():
    built = plan.build_plan(make_dev(), SanitizeCaps(), plan="paranoid")
    assert [p.kind for p in built.passes] == ["zeros", "ones", "zeros", "zeros"]
    assert any("no sanitize support" in note for note in built.notes)
    assert any("skipped" in note for note in built.notes)


def test_crypto_request_falls_back_to_block_erase():
    built = plan.build_plan(
        make_dev(),
        caps(0b001),
        plan="quick",
        sanitize_action="crypto-erase",
    )
    assert built.passes[0].action == "block-erase"
    assert any("does not support crypto erase" in note for note in built.notes)


def test_frozen_sata_keeps_overwrite_passes():
    dev = make_dev(kname="sda", path="/dev/sda", transport="sata")
    built = plan.build_plan(
        dev,
        SanitizeCaps(),
        plan="quick",
        ata=AtaCaps(frozen=True, sanitize_block=True),
    )
    assert [p.kind for p in built.passes] == ["zeros"]
    assert any("frozen" in note.lower() for note in built.notes)
    assert built.sanitize_supported is True


def test_os_disk_is_a_hard_block():
    dev = make_dev(kname="nvme0n1", path="/dev/nvme0n1")
    verdict = safety.check_device(dev, os_runner())
    assert verdict.ok is False
    assert any("operating system" in item for item in verdict.hard_blockers)
    assert verdict.soft_blockers == []


def test_swap_is_a_hard_block():
    dev = make_dev(swap=True)
    verdict = safety.check_device(dev, os_runner("/dev/sda1"))
    assert any("swap" in item for item in verdict.hard_blockers)


def test_mounted_is_soft_until_unmount_is_planned():
    dev = make_dev(
        partitions=[inventory.Partition("nvme1n1p1", 10, ["/mnt/data"])],
    )
    runner = os_runner("/dev/sda1")
    refused = safety.check_device(dev, runner)
    assert refused.ok is False
    assert any("mounted" in item for item in refused.soft_blockers)
    waived = safety.check_device(dev, runner, unmount_planned=True)
    assert waived.soft_blockers == []
    assert waived.ok is True


def test_non_nvme_is_soft_until_allowed():
    dev = make_dev(kname="sda", path="/dev/sda", transport="sata")
    runner = os_runner("/dev/nvme0n1p2")
    refused = safety.check_device(dev, runner)
    assert any("not an NVMe" in item for item in refused.soft_blockers)
    allowed = safety.check_device(dev, runner, allow_non_nvme=True)
    assert allowed.ok is True


def test_recheck_promotes_mounts_to_hard():
    dev = make_dev(
        partitions=[inventory.Partition("nvme1n1p1", 10, ["/mnt/data"])],
    )
    verdict = safety.recheck_before_pass(dev, os_runner("/dev/sda1"), allow_non_nvme=False)
    assert verdict.ok is False
    assert verdict.soft_blockers == []
    assert any("mid-wipe" in item for item in verdict.hard_blockers)


def test_partition_is_not_a_disk():
    dev = make_dev(type="part", kname="nvme1n1p1", path="/dev/nvme1n1p1")
    verdict = safety.check_device(dev, os_runner("/dev/sda1"))
    assert any("not a whole disk" in item for item in verdict.hard_blockers)


def _worker_config(tmp_path: Path) -> Path:
    path = tmp_path / "wipe.json"
    path.write_text(
        json.dumps(
            {
                "device": "/dev/nvme9n1",
                "passes": [],
                "confirmation_word": "DESTROY",
                "confirmation_device": "/dev/nvme9n1",
                "control_dir": str(tmp_path),
            }
        )
    )
    return path


def test_worker_refuses_non_root(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(os, "getuid", lambda: 1000)
    rc = worker.main(_worker_config(tmp_path))
    assert rc == 1
    out = capsys.readouterr().out
    events = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert events[0]["event"] == "hello"
    errors = [ev for ev in events if ev.get("event") == "error"]
    assert errors and "not running as root" in errors[0]["text"]
    assert any("pkexec" in ev.get("text", "") for ev in events)
    assert events[-1]["event"] == "done"
    assert events[-1]["ok"] is False


def test_worker_bad_config_exits_2(tmp_path, capsys):
    path = tmp_path / "wipe.json"
    path.write_text(json.dumps({"device": "/dev/nvme9n1"}))
    assert worker.main(path) == 2
    err = capsys.readouterr().err
    assert "missing keys" in err


def test_worker_rejects_a_non_object_config(tmp_path, capsys):
    path = tmp_path / "wipe.json"
    path.write_text("[]")
    assert worker.main(path) == 2
    assert "object" in capsys.readouterr().err


def test_worker_rejects_an_unreadable_config(tmp_path, capsys):
    missing = tmp_path / "nope.json"
    assert worker.main(missing) == 2
    assert "bad config" in capsys.readouterr().err


def test_read_events_skips_torn_lines():
    lines = [
        '{"event":"hello","uid":0}\n',
        "not-json\n",
        "\n",
        '{"event":"error","text":"lsblk failed"}\n',
        '{"event":"done","ok":false}\n',
    ]
    events = list(protocol.read_events(lines))
    assert [ev["event"] for ev in events] == ["hello", "error", "done"]


def test_parent_disk_names():
    assert safety.parent_disk_of("/dev/nvme0n1p2") == "nvme0n1"
    assert safety.parent_disk_of("/dev/sda3") == "sda"
    assert safety.parent_disk_of("/dev/vda") == "vda"
