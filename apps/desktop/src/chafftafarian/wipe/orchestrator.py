"""orchestrator.py: sequence the wipe passes, safely.

Runs inside the root worker (and, with a FakeToolRunner and a temp-file
"device", in tests). Re-runs the safety gate before every pass, monitors
sanitize via sanitize-log only (other admin commands fail while a sanitize
is in flight), checks the control-dir cancel file between write blocks and
poll ticks, and turns a cancel-during-sanitize into `--sanact=0` followed
by a drain of the log.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from .inventory import DeviceInfo, list_devices
from .nvme import (
    SanitizeCaps,
    parse_sanicap_from_id_ctrl,
    parse_sanitize_log,
    result_or_none,
    sanitize_command,
    sanitize_log_command,
)
from .plan import Pass
from .safety import check_device, recheck_before_pass
from .tools import ToolRunner
from .writer import WipeCancelled, verify_pattern, write_pattern

SANITIZE_POLL_S = 5.0
SANITIZE_TIMEOUT_S = 24 * 3600.0  # big drives legitimately take hours


@dataclass
class WipeConfig:
    device: str  # "/dev/nvme1n1"
    passes: list[dict]  # [{kind: sanitize|zeros|ones|verify, ...}]
    confirmation_word: str = ""
    confirmation_device: str = ""
    control_dir: str = ""  # 0700 dir the GUI owns; <dir>/cancel
    allow_non_nvme: bool = False
    unmount_planned: bool = False
    verify: bool = False
    quick: bool = False
    size_bytes: int | None = None  # optional; else probed from lsblk
    dry_run: bool = False
    dev_dir: str = "/dev"  # tests point this at a temp dir so the
    # "device" is a plain file

    @classmethod
    def from_dict(cls, data: dict) -> WipeConfig:
        return cls(
            device=str(data.get("device") or ""),
            passes=[dict(p) for p in (data.get("passes") or []) if isinstance(p, dict)],
            confirmation_word=str(data.get("confirmation_word") or ""),
            confirmation_device=str(data.get("confirmation_device") or ""),
            control_dir=str(data.get("control_dir") or ""),
            allow_non_nvme=bool(data.get("allow_non_nvme", False)),
            unmount_planned=bool(data.get("unmount_planned", False)),
            verify=bool(data.get("verify", False)),
            quick=bool(data.get("quick", False)),
            size_bytes=(
                int(data["size_bytes"]) if isinstance(data.get("size_bytes"), int) else None
            ),
            dry_run=bool(data.get("dry_run", False)),
            dev_dir=str(data.get("dev_dir") or "/dev"),
        )


@dataclass
class WipeSummary:
    ok: bool
    cancelled: bool = False
    device: str = ""
    passes_total: int = 0
    passes_done: list[int] = field(default_factory=list)
    verify_ok: bool | None = None
    verify_mismatches: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_s: float = 0.0


class WipeOrchestrator:
    def __init__(self, config: WipeConfig, runner: ToolRunner, emit: Callable[..., None]) -> None:
        """`emit(event, **fields)` is protocol.emit_event in the worker and
        a plain callable in tests."""
        self.cfg = config
        self.runner = runner
        self.emit = emit

    # -- control file ---------------------------------------------------------

    def _cancel_requested(self) -> bool:
        return bool(self.cfg.control_dir) and (Path(self.cfg.control_dir) / "cancel").exists()

    # -- device resolution (never trust the GUI's word) ------------------------

    def _resolve_device(self) -> tuple[DeviceInfo | None, str | None]:
        devices = list_devices(self.runner, dev_dir=self.cfg.dev_dir)
        for dev in devices:
            if dev.path == self.cfg.device or f"/dev/{dev.kname}" == self.cfg.device:
                return dev, None
        return None, f"device {self.cfg.device} not found among {len(devices)} disks"

    def _caps(self, dev: DeviceInfo) -> SanitizeCaps:
        raw = result_or_none(self.runner.run(["nvme", "id-ctrl", dev.path], timeout=30))
        return parse_sanicap_from_id_ctrl(raw or "")

    # -- sanitize ----------------------------------------------------------------

    def _sanitize(self, dev: DeviceInfo, pass_: Pass) -> None:
        start = self.runner.run(sanitize_command(dev.path, "block-erase"), timeout=60)
        if not start.ok:
            raise RuntimeError(f"sanitize failed to start: {start.stderr.strip() or start.rc}")
        self.emit("log", text="sanitize started (sanact=2 block erase)")
        time.sleep(2.0)
        deadline = time.monotonic() + SANITIZE_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._cancel_requested():
                self.emit("log", text="cancel during sanitize: aborting (sanact=0)")
                abort = self.runner.run(sanitize_command(dev.path, "abort"), timeout=60)
                if not abort.ok:
                    self.emit(
                        "warning",
                        text=(
                            "sanitize abort command failed: the controller may "
                            "finish anyway: " + abort.stderr.strip()
                        ),
                    )
                self._drain_sanitize(dev)
                raise WipeCancelled("cancelled during sanitize")
            log = self.runner.run(sanitize_log_command(dev.path), timeout=30)
            entry = parse_sanitize_log(log.stdout if log.ok else "")
            self.emit(
                "sanitize_progress",
                passno=pass_.n,
                percent=round(entry.percent, 2),
                sstat=entry.sstat,
                state=entry.state,
            )
            if entry.done:
                if entry.state == "failed":
                    raise RuntimeError(
                        "sanitize completed unsuccessfully (sstat="
                        f"0x{entry.sstat:x}): see TROUBLESHOOTING"
                    )
                self.emit(
                    "log",
                    text=(
                        f"sanitize done: state={entry.state} media_modified={entry.media_modified}"
                    ),
                )
                return
            time.sleep(SANITIZE_POLL_S)
        raise RuntimeError("sanitize exceeded 24h: power-cycle territory; see TROUBLESHOOTING")

    def _drain_sanitize(self, dev: DeviceInfo) -> None:
        """After sanact=0, wait until the status word stops saying
        in-progress (bounded: an aborted sanitize can leave firmware
        opinionated; we do not hang the worker on it)."""
        deadline = time.monotonic() + 300.0
        while time.monotonic() < deadline:
            log = self.runner.run(sanitize_log_command(dev.path), timeout=30)
            entry = parse_sanitize_log(log.stdout if log.ok else "")
            self.emit(
                "sanitize_progress",
                passno=0,
                percent=round(entry.percent, 2),
                sstat=entry.sstat,
                state=entry.state,
            )
            if entry.state != "in-progress":
                return
            time.sleep(SANITIZE_POLL_S)
        self.emit("warning", text="abort drain timed out after 5 minutes")

    def _emit_pass_progress(self, done: int, total: int, *, passno: int) -> None:
        self.emit(
            "pass_progress",
            passno=passno,
            bytes_done=done,
            bytes_total=total,
            percent=round(done * 100.0 / total, 2),
        )

    def _emit_verify_progress(self, done: int, total: int, *, passno: int) -> None:
        self.emit("verify_progress", passno=passno, checked=done, of=total)

    # -- run ------------------------------------------------------------------------

    def run(self) -> WipeSummary:
        started = time.monotonic()
        passes = [
            Pass(n=i + 1, kind=p.get("kind", "zeros"), detail=p.get("detail", ""), est_s=None)
            for i, p in enumerate(self.cfg.passes)
        ]
        summary = WipeSummary(ok=False, device=self.cfg.device, passes_total=len(passes))

        dev, err = self._resolve_device()
        if dev is None:
            summary.errors.append(err or "device resolution failed")
            return summary

        if self.cfg.confirmation_word != "DESTROY" or self.cfg.confirmation_device != dev.path:
            summary.errors.append("confirmation mismatch: refusing")
            return summary

        verdict = check_device(
            dev,
            self.runner,
            allow_non_nvme=self.cfg.allow_non_nvme,
            unmount_planned=self.cfg.unmount_planned,
        )
        if verdict.hard_blockers:
            summary.errors.extend(verdict.hard_blockers)
            return summary
        for note in verdict.soft_blockers:
            summary.warnings.append(note)

        if self.cfg.unmount_planned and dev.mounted:
            for part in dev.partitions:
                for mp in part.mountpoints:
                    if mp == "[SWAP]":
                        continue
                    result = self.runner.run(["umount", mp], timeout=30)
                    self.emit(
                        "log", text=(f"umount {mp}: {'ok' if result.ok else result.stderr.strip()}")
                    )

        if self.cfg.dry_run:
            summary.ok = True
            summary.warnings.append("dry run: no passes executed")
            summary.duration_s = time.monotonic() - started
            return summary

        size = self.cfg.size_bytes or dev.size_bytes
        if not size:
            summary.errors.append("device reports zero size")
            return summary

        try:
            for pass_ in passes:
                if self._cancel_requested():
                    raise WipeCancelled("cancelled between passes")
                # the between-passes gate: mountedness is hard here
                fresh_dev, ferr = self._resolve_device()
                if fresh_dev is None:
                    raise RuntimeError(ferr or "device vanished mid-wipe")
                gate = recheck_before_pass(fresh_dev, self.runner, self.cfg.allow_non_nvme)
                if not gate.ok:
                    raise RuntimeError(
                        "safety re-check failed before pass "
                        f"{pass_.n}: {'; '.join(gate.hard_blockers)}"
                    )

                self.emit("pass_start", passno=pass_.n, kind=pass_.kind, detail=pass_.detail)
                if pass_.kind == "sanitize":
                    self._sanitize(fresh_dev, pass_)
                elif pass_.kind in ("zeros", "ones"):
                    stats = write_pattern(
                        fresh_dev.path,
                        pass_.kind,
                        total_bytes=size,
                        on_progress=partial(self._emit_pass_progress, passno=pass_.n),
                        should_cancel=self._cancel_requested,
                    )
                    if stats.enospc_at_end:
                        summary.warnings.append("ENOSPC on the final partial block (normal)")
                    self.emit(
                        "log",
                        text=(
                            f"pass {pass_.n} done: {stats.bytes_written} bytes, "
                            f"direct={stats.used_direct}"
                        ),
                    )
                elif pass_.kind == "verify":
                    ok, mismatches = verify_pattern(
                        fresh_dev.path,
                        "zeros",
                        total_bytes=size,
                        should_cancel=self._cancel_requested,
                        on_progress=partial(self._emit_verify_progress, passno=pass_.n),
                    )
                    summary.verify_ok = ok
                    summary.verify_mismatches = mismatches[:64]
                    self.emit(
                        "log",
                        text=(
                            f"verification {'intact' if ok else 'found mismatches'}"
                            f" ({len(mismatches)} blocks)"
                        ),
                    )
                else:
                    raise RuntimeError(f"unknown pass kind {pass_.kind!r}")
                summary.passes_done.append(pass_.n)
        except WipeCancelled as exc:
            summary.cancelled = True
            summary.warnings.append(str(exc))
        except Exception as exc:
            summary.errors.append(str(exc))

        summary.ok = (
            not summary.errors
            and not summary.cancelled
            and len(summary.passes_done) == len(passes)
            and summary.verify_ok is not False
        )
        summary.duration_s = time.monotonic() - started
        return summary
