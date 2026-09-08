"""The device layer and wipe pipeline, tested without touching a single
real device: lsblk/nvme/findmnt outputs are scripted into FakeToolRunner,
and the "device" the PatternWriter hits is a temp file.

CI MUST NEVER run these against real hardware: they are constructed so it
cannot (dev_dir points into tmp_path)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chafftafarian.wipe import inventory, nvme, plan, protocol, safety, writer
from chafftafarian.wipe.orchestrator import WipeConfig, WipeOrchestrator
from chafftafarian.wipe.tools import FakeToolRunner

# -- nvme.py parsers ----------------------------------------------------------


class TestNvmeParsers:
    def test_sanitize_log_modern_lowercase(self):
        log = nvme.parse_sanitize_log("sprog : 12345\nsstat : 0x3")
        assert log.sprog == 12345
        assert log.sstat == 3
        assert log.state == "in-progress"
        assert not log.done
        assert 18.8 < log.percent < 18.9

    def test_sanitize_log_verbose_style(self):
        # the shape the NVM Express docs and older nvme-cli print
        log = nvme.parse_sanitize_log(
            "Sanitize Progress (SPROG)\t:\t65535\nSanitize Status (SSTAT)\t:\t0x101"
        )
        assert log.sprog == 65535
        assert log.sstat == 0x101
        assert log.state == "success"
        assert log.media_modified
        assert log.done

    def test_sanitize_states(self):
        for sstat, state in (
            (0x0, "never"),
            (0x1, "success"),
            (0x2, "failed"),
            (0x3, "in-progress"),
            (0x101, "success"),
        ):
            assert nvme.SanitizeLog(0, sstat).state == state

    def test_sanicap_bits(self):
        caps = nvme.parse_sanicap(0b001)
        assert caps.block_erase and not caps.crypto_erase and caps.supported
        caps = nvme.parse_sanicap(0b011)
        assert caps.block_erase and caps.crypto_erase
        assert nvme.parse_sanicap(0).supported is False

    def test_sanicap_from_id_ctrl(self):
        text = "vid : 0x144d\nsanicap : 3\nnn : 1\n"
        caps = nvme.parse_sanicap_from_id_ctrl(text)
        assert caps.block_erase and caps.crypto_erase
        # verbose hex form
        caps = nvme.parse_sanicap_from_id_ctrl("Sanitize Capabilities (SANCAP) : 0x1")
        assert caps.block_erase and not caps.crypto_erase
        # absent field -> nothing supported
        assert nvme.parse_sanicap_from_id_ctrl("vid : 1\n").supported is False

    def test_command_builders(self):
        assert nvme.sanitize_command("/dev/nvme1n1", "block-erase") == [
            "nvme",
            "sanitize",
            "/dev/nvme1n1",
            "--sanact=2",
        ]
        assert nvme.sanitize_command("/dev/nvme1n1", "abort") == [
            "nvme",
            "sanitize",
            "/dev/nvme1n1",
            "--sanact=0",
        ]
        with pytest.raises(ValueError):
            nvme.sanitize_command("/dev/x", "nope")

    def test_nvme_list_json(self):
        parsed = nvme.parse_nvme_list_json(
            '{"Devices":[{"DevicePath":"/dev/nvme0n1","ModelNumber":"X"}]}'
        )
        assert parsed[0]["DevicePath"] == "/dev/nvme0n1"
        assert nvme.parse_nvme_list_json("not json") == []
        assert nvme.parse_nvme_list_json('{"Devices": null}') == []


# -- inventory -----------------------------------------------------------------


def lsblk_result(devices: list[dict]) -> str:
    return json.dumps({"blockdevices": devices})


def nvme_disk(kname="nvme1n1", size=1000204886016, children=None, model="WD SN770", tran="nvme"):
    return {
        "name": kname,
        "kname": kname,
        "type": "disk",
        "size": size,
        "model": model,
        "serial": "SER1",
        "tran": tran,
        "mountpoints": [None],
        "children": children if children is not None else [],
    }


class TestInventory:
    def test_parses_nvme_disk_with_partitions(self):
        dev_node = nvme_disk(
            children=[
                {
                    "name": "nvme1n1p1",
                    "kname": "nvme1n1p1",
                    "type": "part",
                    "size": 999,
                    "mountpoints": ["/mnt/scratch"],
                    "fstype": "ext4",
                    "children": [],
                },
                {
                    "name": "nvme1n1p2",
                    "kname": "nvme1n1p2",
                    "type": "part",
                    "size": 1,
                    "mountpoints": [None],
                    "fstype": None,
                    "children": [],
                },
            ]
        )
        runner = FakeToolRunner(FakeToolRunner.ok(lsblk_result([dev_node])))
        devices = inventory.list_devices(runner)
        assert len(devices) == 1
        dev = devices[0]
        assert dev.kname == "nvme1n1" and dev.path == "/dev/nvme1n1"
        assert dev.is_nvme and dev.is_disk
        assert dev.mounted and dev.mountpoints() == ["/mnt/scratch"]
        assert len(dev.partitions) == 2

    def test_excludes_zram_rom_and_parts(self):
        nodes = [
            nvme_disk(),
            {
                "name": "zram0",
                "kname": "zram0",
                "type": "disk",
                "size": 7,
                "model": None,
                "serial": None,
                "tran": None,
                "mountpoints": ["[SWAP]"],
                "children": [],
            },
            {
                "name": "sr0",
                "kname": "sr0",
                "type": "rom",
                "size": 1,
                "model": None,
                "serial": None,
                "tran": "sata",
                "mountpoints": [None],
                "children": [],
            },
        ]
        runner = FakeToolRunner(FakeToolRunner.ok(lsblk_result(nodes)))
        devices = inventory.list_devices(runner)
        assert [d.kname for d in devices] == ["nvme1n1"]

    def test_swap_detection_and_nested_mountpoints(self):
        # btrfs subvolumes arrive as nested lists on util-linux >= 2.37
        dev_node = nvme_disk(
            children=[
                {
                    "name": "sda1",
                    "kname": "sda1",
                    "type": "part",
                    "size": 5,
                    "mountpoints": [["/mnt/a", "/mnt/a/sub"]],
                    "fstype": "btrfs",
                    "children": [],
                },
                {
                    "name": "sda2",
                    "kname": "sda2",
                    "type": "part",
                    "size": 5,
                    "mountpoints": ["[SWAP]"],
                    "fstype": "swap",
                    "children": [],
                },
            ],
            kname="sda",
            tran="sata",
        )
        runner = FakeToolRunner(FakeToolRunner.ok(lsblk_result([dev_node])))
        dev = inventory.list_devices(runner)[0]
        assert dev.swap
        assert dev.mountpoints() == ["/mnt/a", "/mnt/a/sub"]

    def test_lsblk_failure_yields_empty(self):
        runner = FakeToolRunner(FakeToolRunner.err("lsblk: boom"))
        assert inventory.list_devices(runner) == []

    def test_enrich_with_nvme(self):
        runner = FakeToolRunner(
            FakeToolRunner.ok(lsblk_result([nvme_disk(model=None)])),
            FakeToolRunner.ok(
                '{"Devices":[{"DevicePath":"/dev/nvme1n1",'
                '"ModelNumber":"WD_BLACK SN770",'
                '"SerialNumber":"WD-123"}]}'
            ),
        )
        devices = inventory.enrich_with_nvme(inventory.list_devices(runner), runner)
        assert devices[0].model == "WD_BLACK SN770"


# -- safety ----------------------------------------------------------------------


def os_runner(root_source="/dev/nvme0n1p2"):
    """findmnt answers for the critical mounts; swap is read from the real
    /proc/swaps (asserted by membership, never equality)."""

    def respond(argv):
        if argv[0] == "findmnt":
            return FakeToolRunner.ok(
                f"{root_source}\n" if argv[-1] in safety._CRITICAL_MOUNTS else ""
            )
        return FakeToolRunner.ok("")

    return FakeToolRunner(respond)


def make_dev(**kwargs):
    base = dict(
        kname="nvme1n1",
        path="/dev/nvme1n1",
        type="disk",
        size_bytes=1000204886016,
        model="X",
        serial="S",
        transport="nvme",
    )
    base.update(kwargs)
    return inventory.DeviceInfo(**base)


class TestSafety:
    def test_parent_disk(self):
        assert safety.parent_disk_of("/dev/nvme0n1p12") == "nvme0n1"
        assert safety.parent_disk_of("/dev/nvme0n1") == "nvme0n1"
        assert safety.parent_disk_of("/dev/sda3") == "sda"
        assert safety.parent_disk_of("/dev/vdb") == "vdb"

    def test_os_disk_knames_from_findmnt(self):
        assert "nvme0n1" in safety.os_disk_knames(os_runner())

    def test_os_disk_is_a_hard_block(self):
        dev = make_dev(kname="nvme0n1", path="/dev/nvme0n1")
        verdict = safety.check_device(dev, os_runner())
        assert not verdict.ok
        assert any("operating system" in b for b in verdict.hard_blockers)

    def test_clean_disk_passes(self):
        verdict = safety.check_device(make_dev(), os_runner())
        assert verdict.ok, verdict

    def test_mounted_is_soft_unless_unmount_planned(self):
        dev = make_dev(partitions=[inventory.Partition("nvme1n1p1", 1, ["/mnt/x"])])
        verdict = safety.check_device(dev, os_runner())
        assert not verdict.ok and verdict.soft_blockers and not verdict.hard_blockers
        verdict = safety.check_device(dev, os_runner(), unmount_planned=True)
        assert verdict.ok

    def test_swap_is_hard(self):
        dev = make_dev(swap=True)
        verdict = safety.check_device(dev, os_runner())
        assert any("swap" in b for b in verdict.hard_blockers)

    def test_non_nvme_gated(self):
        dev = make_dev(kname="sdb", path="/dev/sdb", transport="usb", partitions=[])
        verdict = safety.check_device(dev, os_runner())
        assert any("not an NVMe" in b for b in verdict.soft_blockers)
        assert safety.check_device(dev, os_runner(), allow_non_nvme=True).ok

    def test_recheck_makes_mounted_hard(self):
        dev = make_dev(partitions=[inventory.Partition("p1", 1, ["/mnt/x"])])
        verdict = safety.recheck_before_pass(dev, os_runner(), False)
        assert any("appeared mid-wipe" in b for b in verdict.hard_blockers)


# -- plan ---------------------------------------------------------------------------


class TestPlan:
    def caps(self, raw=0b011):
        return nvme.parse_sanicap(raw)

    def test_paranoid_six_with_sanitize(self):
        dev = make_dev()
        p = plan.build_plan(dev, self.caps())
        assert [x.kind for x in p.passes] == [
            "sanitize",
            "zeros",
            "ones",
            "zeros",
            "sanitize",
            "zeros",
        ]
        assert p.passes[1].est_s == 5001  # 1 TB / 200 MB/s

    def test_no_sanitize_drops_brackets_with_notes(self):
        p = plan.build_plan(make_dev(), self.caps(0))
        assert [x.kind for x in p.passes] == ["zeros", "ones", "zeros", "zeros"]
        assert len([n for n in p.notes if "skipped" in n]) == 2

    def test_quick_and_verify(self):
        p = plan.build_plan(make_dev(), self.caps(), quick=True, verify=True)
        assert [x.kind for x in p.passes] == ["sanitize", "zeros", "verify"]
        p = plan.build_plan(make_dev(), self.caps(), verify=True)
        assert p.passes[-1].kind == "verify"


# -- writer ---------------------------------------------------------------------------


class TestWriter:
    def test_write_and_verify_zeros(self, tmp_path):
        target = tmp_path / "disk"
        target.write_bytes(b"\xde\xad" * (writer.BLOCK_SIZE // 2))
        total = writer.BLOCK_SIZE * 3 + 12345
        stats = writer.write_pattern(str(target), "zeros", total_bytes=total)
        assert stats.bytes_written == total
        assert target.stat().st_size == total
        ok, mismatches = writer.verify_pattern(str(target), "zeros", total_bytes=total)
        assert ok, mismatches

    def test_verify_catches_leftover_pattern(self, tmp_path):
        target = tmp_path / "disk"
        target.write_bytes(b"x" * 16)
        total = writer.BLOCK_SIZE * 2
        writer.write_pattern(str(target), "zeros", total_bytes=total)
        # plant 0xFF in the FIRST block, which every sample checks
        with open(target, "r+b") as fh:
            fh.seek(512)
            fh.write(b"\xff" * 16)
        ok, mismatches = writer.verify_pattern(str(target), "zeros", total_bytes=total)
        assert not ok
        assert mismatches and mismatches[0]["expected"] == "zeros"

    def test_cancel_mid_write(self, tmp_path):
        target = tmp_path / "disk"
        target.write_bytes(b"x" * 16)
        with pytest.raises(writer.WipeCancelled):
            writer.write_pattern(
                str(target), "ones", total_bytes=writer.BLOCK_SIZE * 4, should_cancel=lambda: True
            )

    def test_direct_or_fallback_both_write(self, tmp_path):
        # tmpfs refuses O_DIRECT: exercises the EINVAL fallback path
        target = tmp_path / "disk"
        target.write_bytes(b"x" * 16)
        writer.write_pattern(str(target), "ones", total_bytes=writer.BLOCK_SIZE * 2)
        data = target.read_bytes()
        assert data.count(b"\xff") == len(data)


# -- orchestrator: fake end-to-end wipe on a temp file ---------------------------------


class FakeSanitizeRunner(FakeToolRunner):
    """lsblk shows our file-backed "disk"; findmnt says nothing critical;
    id-ctrl reports sanicap=1; sanitize-log walks to completion."""

    def __init__(self, disk_path: Path, size: int, sanicap: int = 1):
        super().__init__()
        self.disk_path = disk_path
        self.sanitize_sprog = 0
        kname = disk_path.name
        self.lsblk = json.dumps(
            {
                "blockdevices": [
                    {
                        "name": kname,
                        "kname": kname,
                        "type": "disk",
                        "size": size,
                        "model": "Fake",
                        "serial": "F1",
                        "tran": "nvme",
                        "mountpoints": [None],
                        "children": [],
                    }
                ]
            }
        )

    def run(self, argv, timeout=None):
        self.calls.append(list(argv))
        prog = argv[0]
        if prog == "lsblk":
            return FakeToolRunner.ok(self.lsblk)
        if prog == "findmnt":
            return FakeToolRunner.ok("")
        if prog == "nvme" and argv[1] == "id-ctrl":
            return FakeToolRunner.ok("sanicap : 1\n")
        if prog == "nvme" and argv[1] == "sanitize" and "--sanact=2" in argv:
            self.sanitize_sprog = 1
            return FakeToolRunner.ok("sanitized")
        if prog == "nvme" and argv[1] == "sanitize-log":
            done = min(65535, self.sanitize_sprog)
            self.sanitize_sprog = min(65535, self.sanitize_sprog + 40000)
            return FakeToolRunner.ok(
                f"sprog : {done}\nsstat : 0x3\n"
                if done < 65535
                else "sprog : 65535\nsstat : 0x101\n"
            )
        return FakeToolRunner.ok("")


class TestOrchestrator:
    def config(self, tmp_path, passes, **kw):
        device = tmp_path / "fakedisk"
        device.write_bytes(b"\xaa" * (writer.BLOCK_SIZE * 2 + 5000))
        control = tmp_path / "control"
        control.mkdir(exist_ok=True)
        cfg = WipeConfig(
            device=str(device),
            passes=passes,
            confirmation_word="DESTROY",
            confirmation_device=str(device),
            control_dir=str(control),
            size_bytes=writer.BLOCK_SIZE * 2 + 5000,
            dev_dir=str(tmp_path),
            # the fake "disk" is a temp file, not an /dev/nvme* node: the
            # non-NVMe waiver is part of the fixture, not the behavior
            allow_non_nvme=True,
            **kw,
        )
        return cfg, device, control

    def events(self):
        out = []

        def emit(event, **fields):
            out.append({"event": event, **fields})

        return out, emit

    def test_end_to_end_wipe_with_verify(self, tmp_path):
        cfg, device, _control = self.config(
            tmp_path, [{"kind": "zeros"}, {"kind": "ones"}, {"kind": "zeros"}, {"kind": "verify"}]
        )
        events, emit = self.events()
        runner = FakeSanitizeRunner(device, cfg.size_bytes)
        summary = WipeOrchestrator(cfg, runner, emit).run()
        assert summary.ok, summary.errors
        assert summary.passes_done == [1, 2, 3, 4]
        assert summary.verify_ok is True
        assert device.read_bytes().count(0) == cfg.size_bytes  # ends zeroed
        kinds = [e["event"] for e in events]
        assert kinds[0] == "pass_start" and "pass_progress" in kinds
        assert "verify_progress" in kinds

    def test_dry_run_writes_nothing(self, tmp_path):
        cfg, device, _control = self.config(tmp_path, [{"kind": "zeros"}], dry_run=True)
        before = device.read_bytes()
        summary = WipeOrchestrator(
            cfg, FakeSanitizeRunner(device, cfg.size_bytes), lambda *a, **k: None
        ).run()
        assert summary.ok and device.read_bytes() == before

    def test_confirmation_mismatch_refuses(self, tmp_path):
        cfg, device, _control = self.config(tmp_path, [{"kind": "zeros"}])
        cfg.confirmation_device = "/dev/someone-else"
        summary = WipeOrchestrator(cfg, FakeSanitizeRunner(device, 1), lambda *a, **k: None).run()
        assert not summary.ok and "confirmation" in summary.errors[0]

    def test_cancel_mid_write(self, tmp_path):
        cfg, device, control = self.config(tmp_path, [{"kind": "zeros"}])
        (control / "cancel").write_text("stop")
        summary = WipeOrchestrator(
            cfg, FakeSanitizeRunner(device, cfg.size_bytes), lambda *a, **k: None
        ).run()
        assert summary.cancelled and not summary.ok

    def test_cancel_mid_sanitize_aborts(self, tmp_path):
        cfg, device, control = self.config(tmp_path, [{"kind": "sanitize"}])
        _events, emit = self.events()
        runner = FakeSanitizeRunner(device, 1)
        # cancel as soon as the sanitize starts
        real_run = runner.run

        def run_cancel(argv, timeout=None):
            result = real_run(argv, timeout)
            if argv[:2] == ["nvme", "sanitize"]:
                (control / "cancel").write_text("stop")
            return result

        runner.run = run_cancel
        summary = WipeOrchestrator(cfg, runner, emit).run()
        assert summary.cancelled
        aborts = [c for c in runner.calls if c[:2] == ["nvme", "sanitize"] and "--sanact=0" in c]
        assert aborts, "expected a sanact=0 abort"

    def test_sanitize_failure_raises(self, tmp_path):
        cfg, device, _control = self.config(tmp_path, [{"kind": "sanitize"}])
        runner = FakeSanitizeRunner(device, 1)

        def run_fail(argv, timeout=None):
            if argv[1:2] == ["sanitize-log"]:
                runner.calls.append(list(argv))
                return FakeToolRunner.ok("sprog : 65535\nsstat : 0x102\n")
            return FakeSanitizeRunner.run(runner, argv, timeout)

        runner.run = run_fail
        summary = WipeOrchestrator(cfg, runner, lambda *a, **k: None).run()
        assert not summary.ok and "unsuccessfully" in summary.errors[0]


# -- protocol / worker config ---------------------------------------------------------


class TestProtocol:
    def test_read_events_skips_torn_lines(self):
        lines = ['{"event": "hello"}', "garbage{", '{"event": "done"}', ""]
        events = list(protocol.read_events(iter(lines)))
        assert [e["event"] for e in events] == ["hello", "done"]

    def test_required_keys(self):
        assert set(protocol.config_required_keys()) >= {
            "device",
            "passes",
            "confirmation_word",
            "confirmation_device",
            "control_dir",
        }

    def test_worker_rejects_bad_config(self, tmp_path, capsys):
        from chafftafarian.wipe import worker

        bad = tmp_path / "bad.json"
        bad.write_text('{"device": "/dev/nvme0n1"}')  # missing keys
        rc = worker.main(bad)
        assert rc == 2
        assert "missing keys" in capsys.readouterr().err

    def test_worker_refuses_non_root(self, tmp_path, monkeypatch):
        import io

        from chafftafarian.wipe import worker

        cfg = tmp_path / "wipe.json"
        cfg.write_text(
            json.dumps(
                {
                    "device": "/dev/nvme1n1",
                    "passes": [{"kind": "zeros"}],
                    "confirmation_word": "DESTROY",
                    "confirmation_device": "/dev/nvme1n1",
                    "control_dir": str(tmp_path),
                    "dry_run": True,
                }
            )
        )
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)
        if worker.os.getuid() == 0:
            pytest.skip("running as root; non-root refusal untestable")
        rc = worker.main(cfg)
        assert rc == 1
        out = captured.getvalue()
        assert '"event": "error"' in out and "not running as root" in out
        assert out.strip().endswith('{"event": "done", "ok": false, "cancelled": false}')


def test_worker_module_dispatch(tmp_path):
    """`python -m chafftafarian --wipe-worker` reaches the worker without
    importing Qt (the headless-root requirement)."""
    import subprocess

    cfg = tmp_path / "wipe.json"
    cfg.write_text(
        json.dumps(
            {
                "device": "/dev/nvme1n1",
                "passes": [{"kind": "zeros"}],
                "confirmation_word": "DESTROY",
                "confirmation_device": "/dev/nvme1n1",
                "control_dir": str(tmp_path),
                "dry_run": True,
            }
        )
    )
    proc = subprocess.run(
        [sys.executable, "-m", "chafftafarian", "--wipe-worker", "--config", str(cfg)],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
        timeout=60,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if proc.returncode == 0:  # ran as root (CI must not)
        assert lines[0].startswith('{"event": "hello"')
    else:
        assert any('"event": "error"' in ln for ln in lines)
