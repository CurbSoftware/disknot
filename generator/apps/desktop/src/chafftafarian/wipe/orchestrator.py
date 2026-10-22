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

from .ata import (
    ATA_ACTIONS,
    identify_command,
    parse_hdparm_identify,
    parse_sanitize_status,
    sanitize_block_erase_command,
    sanitize_crypto_scramble_command,
    sanitize_status_command,
    security_disable_command,
    security_erase_command,
    security_set_pass_command,
)
from .inventory import DeviceInfo, list_devices
from .nvme import (
    NVME_ACTIONS,
    SanitizeCaps,
    parse_sanicap_from_id_ctrl,
    parse_sanitize_log,
    result_or_none,
    sanact_for,
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
    device: str = ""  # "/dev/nvme1n1"
    passes: list[dict] = field(default_factory=list)  # [{kind: sanitize|zeros|ones|verify, ...}]
    targets: list[dict] = field(default_factory=list)  # [{"device": ..., "steps": [...]}]
    confirmation_word: str = ""
    confirmation_device: str = ""
    confirmation_devices: list[str] = field(default_factory=list)
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
            targets=[dict(t) for t in (data.get("targets") or []) if isinstance(t, dict)],
            confirmation_word=str(data.get("confirmation_word") or ""),
            confirmation_device=str(data.get("confirmation_device") or ""),
            confirmation_devices=[str(d) for d in (data.get("confirmation_devices") or []) if d],
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
    targets_done: list[str] = field(default_factory=list)
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

    def _resolve_device(self, dev_path: str | None = None) -> tuple[DeviceInfo | None, str | None]:
        target = dev_path or self.cfg.device
        devices = list_devices(self.runner, dev_dir=self.cfg.dev_dir)
        for dev in devices:
            if dev.path == target or f"/dev/{dev.kname}" == target:
                return dev, None
        return None, f"device {target} not found among {len(devices)} disks"

    def _caps(self, dev: DeviceInfo) -> SanitizeCaps:
        raw = result_or_none(self.runner.run(["nvme", "id-ctrl", dev.path], timeout=30))
        return parse_sanicap_from_id_ctrl(raw or "")

    # -- sanitize ----------------------------------------------------------------

    def _sanitize(self, dev: DeviceInfo, pass_: Pass) -> None:
        action = pass_.action if pass_.action in NVME_ACTIONS else "block-erase"
        self._abort_in_progress_if_needed(dev)
        start = self.runner.run(sanitize_command(dev.path, action), timeout=60)
        if not start.ok:
            raise RuntimeError(f"sanitize failed to start: {start.stderr.strip() or start.rc}")
        sanact = sanact_for(action)
        self.emit("log", text=f"sanitize started (sanact={sanact} {action})")
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

    def _abort_in_progress_if_needed(self, dev: DeviceInfo) -> None:
        log = self.runner.run(sanitize_log_command(dev.path), timeout=30)
        entry = parse_sanitize_log(log.stdout if log.ok else "")
        if entry.state != "in-progress":
            return
        self.emit("log", text="sanitize already in progress: aborting (sanact=0) before start")
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

    def _ata_sanitize(self, dev: DeviceInfo, pass_: Pass) -> None:
        action = pass_.action if pass_.action in ATA_ACTIONS else "sata-block-erase"
        ident_raw = result_or_none(self.runner.run(identify_command(dev.path), timeout=15))
        caps = parse_hdparm_identify(ident_raw or "")
        if caps.frozen:
            raise RuntimeError("this drive is frozen: firmware erase refused; power-cycle it")
        if action == "sata-secure-erase":
            self._ata_secure_erase(dev, caps.security_erase_enhanced)
            return
        if action == "sata-crypto":
            argv = sanitize_crypto_scramble_command(dev.path)
        else:
            argv = sanitize_block_erase_command(dev.path)
        start = self.runner.run(argv, timeout=60)
        if not start.ok:
            raise RuntimeError(
                f"hdparm sanitize failed to start: {start.stderr.strip() or start.rc}"
            )
        self.emit("log", text=f"hdparm sanitize started ({action})")
        time.sleep(2.0)
        deadline = time.monotonic() + SANITIZE_TIMEOUT_S
        cancel_seen = False
        while time.monotonic() < deadline:
            if self._cancel_requested() and not cancel_seen:
                cancel_seen = True
                self.emit(
                    "warning",
                    text=(
                        "ATA sanitize cannot be aborted from the host. Waiting for the controller."
                    ),
                )
            status = self.runner.run(sanitize_status_command(dev.path), timeout=30)
            entry = parse_sanitize_status((status.stdout or "") + "\n" + (status.stderr or ""))
            percent = 100.0 if entry.state == "success" else 0.0
            self.emit(
                "sanitize_progress",
                passno=pass_.n,
                percent=percent,
                state=entry.state,
            )
            if entry.done:
                if entry.state != "success":
                    raise RuntimeError(f"hdparm sanitize ended ({entry.state}): see the wipe log")
                self.emit("log", text=f"hdparm sanitize done ({action})")
                if cancel_seen:
                    raise WipeCancelled("cancelled during ATA sanitize")
                return
            time.sleep(SANITIZE_POLL_S)
        raise RuntimeError("hdparm sanitize exceeded 24h: power-cycle territory")

    def _ata_secure_erase(self, dev: DeviceInfo, enhanced: bool) -> None:
        setp = self.runner.run(security_set_pass_command(dev.path), timeout=60)
        if not setp.ok:
            raise RuntimeError(f"hdparm security-set-pass failed: {setp.stderr.strip() or setp.rc}")
        self.emit("log", text="hdparm security password set (p); starting erase")
        erase = self.runner.run(
            security_erase_command(dev.path, enhanced=enhanced),
            timeout=SANITIZE_TIMEOUT_S,
        )
        disable = self.runner.run(security_disable_command(dev.path), timeout=60)
        if not disable.ok:
            self.emit(
                "warning",
                text=(
                    "hdparm security-disable failed: the drive may stay locked "
                    "with password p: " + disable.stderr.strip()
                ),
            )
        if not erase.ok:
            raise RuntimeError(f"hdparm security-erase failed: {erase.stderr.strip() or erase.rc}")
        self.emit("log", text="hdparm security-erase done")
        if self._cancel_requested():
            raise WipeCancelled("cancelled during security-erase")

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
        targets = self.cfg.targets
        if not targets and self.cfg.device:
            targets = [
                {
                    "device": self.cfg.device,
                    "steps": self.cfg.passes,
                    "size_bytes": self.cfg.size_bytes,
                }
            ]

        total_steps = sum(len(t.get("steps") or t.get("passes") or []) for t in targets)
        primary_dev = targets[0].get("device", self.cfg.device) if targets else self.cfg.device
        summary = WipeSummary(ok=False, device=primary_dev, passes_total=total_steps)

        if not targets:
            summary.errors.append("no target devices configured")
            return summary

        # Check confirmation word
        if self.cfg.confirmation_word != "DESTROY":
            summary.errors.append("confirmation mismatch: refusing")
            return summary

        # Check confirmation devices
        target_devs = [str(t.get("device", "")) for t in targets if t.get("device")]
        if self.cfg.confirmation_devices:
            if set(self.cfg.confirmation_devices) != set(target_devs):
                summary.errors.append("confirmation mismatch: refusing")
                return summary
        elif self.cfg.confirmation_device and (
            len(target_devs) != 1 or self.cfg.confirmation_device != target_devs[0]
        ):
            summary.errors.append("confirmation mismatch: refusing")
            return summary

        # Validate each target device before any action
        resolved_targets: list[tuple[dict, DeviceInfo]] = []
        for target_cfg in targets:
            dev_path = target_cfg.get("device", "")
            dev, err = self._resolve_device(dev_path)
            if dev is None:
                summary.errors.append(err or f"device resolution failed for {dev_path}")
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
            resolved_targets.append((target_cfg, dev))

        if self.cfg.dry_run:
            summary.ok = True
            summary.warnings.append("dry run: no passes executed")
            summary.duration_s = time.monotonic() - started
            return summary

        is_multi_target = bool(self.cfg.targets)
        global_pass_counter = 0
        try:
            for t_idx, (target_cfg, initial_dev) in enumerate(resolved_targets):
                if self._cancel_requested():
                    raise WipeCancelled("cancelled between targets")

                dev_path = initial_dev.path
                if is_multi_target:
                    self.emit(
                        "target_start",
                        target_index=t_idx + 1,
                        targets_total=len(resolved_targets),
                        device=dev_path,
                    )

                if self.cfg.unmount_planned and initial_dev.mounted:
                    for part in initial_dev.partitions:
                        for mp in part.mountpoints:
                            if mp == "[SWAP]":
                                continue
                            result = self.runner.run(["umount", mp], timeout=30)
                            self.emit(
                                "log",
                                text=(
                                    f"umount {mp}: {'ok' if result.ok else result.stderr.strip()}"
                                ),
                            )

                target_size = (
                    target_cfg.get("size_bytes") or self.cfg.size_bytes or initial_dev.size_bytes
                )
                if not target_size:
                    raise RuntimeError(f"device {dev_path} reports zero size")

                steps_raw = target_cfg.get("steps") or target_cfg.get("passes") or []
                passes = [
                    Pass(
                        n=i + 1,
                        kind=p.get("kind", "zeros"),
                        detail=p.get("detail", ""),
                        est_s=None,
                        action=p.get("action"),
                    )
                    for i, p in enumerate(steps_raw)
                ]

                for pass_ in passes:
                    global_pass_counter += 1
                    if self._cancel_requested():
                        raise WipeCancelled("cancelled between passes")

                    # the between-passes gate: mountedness is hard here
                    fresh_dev, ferr = self._resolve_device(dev_path)
                    if fresh_dev is None:
                        raise RuntimeError(ferr or f"device {dev_path} vanished mid-wipe")
                    gate = recheck_before_pass(fresh_dev, self.runner, self.cfg.allow_non_nvme)
                    if not gate.ok:
                        raise RuntimeError(
                            f"safety re-check failed before pass {pass_.n} on {dev_path}: "
                            f"{'; '.join(gate.hard_blockers)}"
                        )

                    pass_fields = {
                        "passno": global_pass_counter,
                        "kind": pass_.kind,
                        "detail": pass_.detail,
                    }
                    if is_multi_target:
                        pass_fields["device"] = dev_path
                    self.emit("pass_start", **pass_fields)

                    if pass_.kind in ("nvme_reset", "sanitize"):
                        if (pass_.action or "").startswith("sata-"):
                            self._ata_sanitize(fresh_dev, pass_)
                        else:
                            self._sanitize(fresh_dev, pass_)
                    elif pass_.kind == "ata_erase":
                        self._ata_sanitize(fresh_dev, pass_)
                    elif pass_.kind == "shred":
                        stats = write_pattern(
                            fresh_dev.path,
                            "random",
                            total_bytes=target_size,
                            on_progress=partial(
                                self._emit_pass_progress, passno=global_pass_counter
                            ),
                            should_cancel=self._cancel_requested,
                        )
                        if stats.enospc_at_end:
                            summary.warnings.append("ENOSPC on the final partial block (normal)")
                        self.emit(
                            "log",
                            text=(
                                f"pass {pass_.n} (shred) done on {dev_path}: "
                                f"{stats.bytes_written} bytes, direct={stats.used_direct}"
                            ),
                        )
                    elif pass_.kind in ("zeros", "ones"):
                        stats = write_pattern(
                            fresh_dev.path,
                            pass_.kind,
                            total_bytes=target_size,
                            on_progress=partial(
                                self._emit_pass_progress, passno=global_pass_counter
                            ),
                            should_cancel=self._cancel_requested,
                        )
                        if stats.enospc_at_end:
                            summary.warnings.append("ENOSPC on the final partial block (normal)")
                        self.emit(
                            "log",
                            text=(
                                f"pass {pass_.n} ({pass_.kind}) done on {dev_path}: "
                                f"{stats.bytes_written} bytes, direct={stats.used_direct}"
                            ),
                        )
                    elif pass_.kind == "overwrite_x":
                        opts = target_cfg.get("options")
                        p_count = int(opts.get("passes", 3) if isinstance(opts, dict) else 3)
                        patterns = ["random", "ones", "zeros"]
                        for p_idx in range(p_count):
                            if self._cancel_requested():
                                raise WipeCancelled("cancelled during overwrite_x")
                            pat = patterns[p_idx % len(patterns)]
                            self.emit(
                                "log",
                                text=f"overwrite pass {p_idx + 1}/{p_count} ({pat}) on {dev_path}",
                            )
                            write_pattern(
                                fresh_dev.path,
                                pat,
                                total_bytes=target_size,
                                on_progress=partial(
                                    self._emit_pass_progress, passno=global_pass_counter
                                ),
                                should_cancel=self._cancel_requested,
                            )
                    elif pass_.kind == "chaff_create":
                        from .chaff_step import create_chaff_on_device

                        ok, msg = create_chaff_on_device(
                            fresh_dev.path,
                            self.runner,
                            should_cancel=self._cancel_requested,
                        )
                        if not ok:
                            summary.warnings.append(f"chaff_create warning on {dev_path}: {msg}")
                        self.emit("log", text=f"chaff_create on {dev_path}: {msg}")
                    elif pass_.kind == "chaff_delete":
                        from .chaff_step import delete_chaff_on_device

                        ok, msg = delete_chaff_on_device(fresh_dev.path, self.runner)
                        self.emit("log", text=f"chaff_delete on {dev_path}: {msg}")
                    elif pass_.kind == "verify":
                        ok, mismatches = verify_pattern(
                            fresh_dev.path,
                            "zeros",
                            total_bytes=target_size,
                            should_cancel=self._cancel_requested,
                            on_progress=partial(
                                self._emit_verify_progress, passno=global_pass_counter
                            ),
                        )
                        summary.verify_ok = ok if summary.verify_ok is not False else False
                        summary.verify_mismatches.extend(mismatches[:64])
                        res_label = "intact" if ok else "found mismatches"
                        self.emit(
                            "log",
                            text=(
                                f"verification on {dev_path}: "
                                f"{res_label} ({len(mismatches)} blocks)"
                            ),
                        )
                    else:
                        raise RuntimeError(f"unknown pass kind {pass_.kind!r}")
                    summary.passes_done.append(global_pass_counter)

                summary.targets_done.append(dev_path)
                if is_multi_target:
                    self.emit("target_done", target_index=t_idx + 1, device=dev_path)

        except WipeCancelled as exc:
            summary.cancelled = True
            summary.warnings.append(str(exc))
        except Exception as exc:
            summary.errors.append(str(exc))

        summary.ok = (
            not summary.errors
            and not summary.cancelled
            and len(summary.passes_done) == total_steps
            and summary.verify_ok is not False
        )
        summary.duration_s = time.monotonic() - started
        return summary
