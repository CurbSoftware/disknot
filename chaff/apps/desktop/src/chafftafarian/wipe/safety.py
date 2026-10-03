"""safety.py: the refusal layer. Nothing here is a convenience.

`check_device` produces a verdict the GUI renders BEFORE any confirmation
and the root worker re-runs before EVERY pass. Hard refusals (os_disk,
swap) have no override anywhere in the app; soft refusals (mounted, non-
NVMe) are explicitly waived by user choices that ride the wipe config.

Fixes the gaps inherited from secure_wipe.sh: it never checked whether the
target hosted the running OS, umounted silently with `|| true`, and never
re-checked mountedness before later passes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .inventory import DeviceInfo
from .tools import ToolRunner

# A device hosting any of these mountpoints is the running OS and is never
# wipeable. /usr, /boot, /etc can be separate partitions on the same disk.
_CRITICAL_MOUNTS = ("/", "/usr", "/boot", "/boot/efi", "/etc", "/var")


@dataclass
class SafetyVerdict:
    ok: bool
    hard_blockers: list[str] = field(default_factory=list)  # never overridable
    soft_blockers: list[str] = field(default_factory=list)  # waived by config
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "hard_blockers": self.hard_blockers,
            "soft_blockers": self.soft_blockers,
            "notes": self.notes,
        }


def parent_disk_of(device_or_part: str) -> str:
    """Best-effort whole-disk kname of a partition or disk path.
    /dev/nvme0n1p2 -> nvme0n1; /dev/sda3 -> sda; /dev/vda -> vda."""
    name = device_or_part.removeprefix("/dev/")
    if name.startswith("nvme"):
        # nvme0n1p2 -> nvme0n1 (only strip a trailing p<digits>)
        idx = name.rfind("p")
        if idx > 0 and name[idx + 1 :].isdigit():
            return name[:idx]
        return name
    stripped = name.rstrip("0123456789")
    return stripped if stripped else name


def os_disk_knames(runner: ToolRunner) -> set[str]:
    """Whole-disk knames behind the critical mountpoints and active swap.
    Reads findmnt + /proc/swaps; never raises."""
    knames: set[str] = set()
    for mp in _CRITICAL_MOUNTS:
        result = runner.run(["findmnt", "-n", "-o", "SOURCE", mp], timeout=10)
        first = result.stdout.strip().splitlines()[:1]
        source = first[0].strip() if first else ""
        if source.startswith("/dev/"):
            knames.add(parent_disk_of(source))
    try:
        with open("/proc/swaps", encoding="utf-8", errors="replace") as fh:
            swaps = fh.read()
    except OSError:
        swaps = ""
    for line in swaps.splitlines()[1:]:
        fields = line.split()
        if fields and fields[0].startswith("/dev/"):
            knames.add(parent_disk_of(fields[0]))
    return knames


def check_device(
    dev: DeviceInfo,
    runner: ToolRunner,
    *,
    allow_non_nvme: bool = False,
    unmount_planned: bool = False,
) -> SafetyVerdict:
    """Verdict for wiping `dev`. `allow_non_nvme` mirrors the app setting
    (off by default; exists for SATA/USB/loop). `unmount_planned` waives the
    mounted-refusal: the worker will unmount, logged, and re-check."""
    verdict = SafetyVerdict(ok=True)

    if not dev.is_disk:
        verdict.hard_blockers.append(f"{dev.path} is a {dev.type}, not a whole disk")
    if dev.kname in os_disk_knames(runner):
        verdict.hard_blockers.append(
            f"{dev.path} hosts the running operating system: never wipeable"
        )
    if dev.swap:
        verdict.hard_blockers.append(
            f"{dev.path} has active swap: swapoff it (or its parent) first"
        )

    if dev.mounted and not unmount_planned:
        mounts = ", ".join(dev.mountpoints()) or "unknown mountpoints"
        verdict.soft_blockers.append(f"mounted: {mounts}: unmount first, or let the worker do it")
    if not dev.is_nvme and not allow_non_nvme:
        verdict.soft_blockers.append(
            "not an NVMe device: enable non-NVMe devices in settings (testing path) to waive"
        )

    verdict.ok = not verdict.hard_blockers and not verdict.soft_blockers
    return verdict


def recheck_before_pass(dev: DeviceInfo, runner: ToolRunner, allow_non_nvme: bool) -> SafetyVerdict:
    """The between-passes gate: mountedness is a hard refusal here because
    the unmount already happened: anything mounted appeared since."""
    verdict = SafetyVerdict(ok=True)
    if not dev.is_disk:
        verdict.hard_blockers.append(f"{dev.path} is not a whole disk")
    if dev.kname in os_disk_knames(runner):
        verdict.hard_blockers.append(f"{dev.path} hosts the running OS")
    if dev.mounted:
        verdict.hard_blockers.append(
            f"{dev.path} has mounts that appeared mid-wipe: {', '.join(dev.mountpoints())}"
        )
    if dev.swap:
        verdict.hard_blockers.append(f"{dev.path} has active swap")
    if not dev.is_nvme and not allow_non_nvme:
        verdict.hard_blockers.append(f"{dev.path} is not NVMe")
    verdict.ok = not verdict.hard_blockers
    return verdict
