"""inventory.py: enumerate block devices via lsblk (JSON, byte sizes),
enriched with nvme-cli detail when available.

lsblk -Jb -o NAME,SIZE,TYPE,KNAME,MODEL,SERIAL,TRAN,MOUNTPOINTS,CHILDREN
is parsed defensively: every field may be null, and mountpoints is a list
that mixes nulls, paths, "[SWAP]", and (on newer util-linux) nested
subvolume lists.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .nvme import nvme_list_command, parse_nvme_list_json, result_or_none
from .tools import ToolRunner


@dataclass
class Partition:
    name: str
    size_bytes: int
    mountpoints: list[str] = field(default_factory=list)
    fstype: str | None = None


@dataclass
class DeviceInfo:
    kname: str  # kernel name, e.g. "nvme0n1" (no /dev prefix)
    path: str  # "/dev/nvme0n1"
    type: str  # lsblk TYPE: disk | part | rom | loop | ...
    size_bytes: int
    model: str | None = None
    serial: str | None = None
    transport: str | None = None  # nvme | sata | usb | null
    partitions: list[Partition] = field(default_factory=list)
    # filled by safety/os detection or nvme enrichment
    os_disk: bool = False
    swap: bool = False
    sanitize_caps: dict | None = None  # from id-ctrl when probed

    @property
    def is_nvme(self) -> bool:
        return self.kname.startswith("nvme")

    @property
    def is_disk(self) -> bool:
        return self.type == "disk"

    @property
    def mounted(self) -> bool:
        return any(p.mountpoints for p in self.partitions)

    def mountpoints(self) -> list[str]:
        """Real mount points only: "[SWAP]" is reported via the swap flag."""
        out: list[str] = []
        for p in self.partitions:
            out.extend(mp for mp in p.mountpoints if mp != "[SWAP]")
        return out


_LSBLK_ARGS = [
    "lsblk",
    "-Jb",
    "-o",
    "NAME,SIZE,TYPE,KNAME,MODEL,SERIAL,TRAN,MOUNTPOINTS,FSTYPE,CHILDREN",
]


def _flat_mountpoints(raw: object) -> list[str]:
    """mountpoints entries may be null, a path string, "[SWAP]", or a list
    of paths (btrfs subvolumes on util-linux >= 2.37)."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        out = []
        for item in raw:
            out.extend(_flat_mountpoints(item))
        return out
    return []


def _parse_device(node: dict) -> DeviceInfo | None:
    if not isinstance(node, dict):
        return None
    kname = str(node.get("kname") or node.get("name") or "").strip()
    if not kname or str(node.get("type")) != "disk":
        return None
    size = node.get("size")
    dev = DeviceInfo(
        kname=kname,
        path=f"/dev/{kname}",
        type=str(node.get("type")),
        size_bytes=int(size) if isinstance(size, (int, float)) else 0,
        model=node.get("model"),
        serial=node.get("serial"),
        transport=node.get("tran"),
    )
    for child in node.get("children") or []:
        if not isinstance(child, dict):
            continue
        csize = child.get("size")
        dev.partitions.append(
            Partition(
                name=str(child.get("name") or ""),
                size_bytes=int(csize) if isinstance(csize, (int, float)) else 0,
                mountpoints=_flat_mountpoints(child.get("mountpoints")),
                fstype=child.get("fstype"),
            )
        )
        if "[SWAP]" in dev.partitions[-1].mountpoints:
            dev.swap = True
    # whole-disk mount (no partition table): rare but real
    for mp in _flat_mountpoints(node.get("mountpoints")):
        if mp == "[SWAP]":
            dev.swap = True
    return dev


def list_devices(runner: ToolRunner, *, dev_dir: str = "/dev") -> list[DeviceInfo]:
    """All whole disks lsblk reports. Never raises; an lsblk failure yields
    []. zram devices (RAM-backed swap) are excluded: wiping RAM disks is
    meaningless and only clutters the drive bay. `dev_dir` rewrites the
    /dev prefix: tests point it at a temp dir whose "devices" are files."""
    import json

    result = runner.run(_LSBLK_ARGS, timeout=15)
    if not result.ok:
        return []
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return []
    nodes = data.get("blockdevices") if isinstance(data, dict) else None
    if not isinstance(nodes, list):
        return []
    devices: list[DeviceInfo] = []
    for node in nodes:
        dev = _parse_device(node)
        if dev is None or dev.kname.startswith(("zram", "ram", "sr")):
            continue
        dev.path = f"{dev_dir}/{dev.kname}"
        devices.append(dev)
    return devices


def enrich_with_nvme(devices: list[DeviceInfo], runner: ToolRunner) -> list[DeviceInfo]:
    """Attach model/serial from `nvme list -o json` when nvme-cli is present.
    Model strings from nvme-cli are richer than sysfs; lsblk stays the
    source of truth for geometry."""
    raw = result_or_none(runner.run(nvme_list_command(), timeout=15))
    if not raw:
        return devices
    by_path = {d["DevicePath"]: d for d in parse_nvme_list_json(raw) if d.get("DevicePath")}
    for dev in devices:
        entry = by_path.get(dev.path) or by_path.get(f"/dev/{dev.kname}")
        if entry:
            dev.model = dev.model or entry.get("ModelNumber")
            dev.serial = dev.serial or entry.get("SerialNumber")
    return devices
