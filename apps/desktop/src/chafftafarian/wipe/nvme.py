"""nvme.py: parse nvme-cli's identify and sanitize-status output.

Decode tables (validated against the nvme-cli documentation; see
docs/SPEC.md "Sanitize decode tables" for sources and the fixture caveat):

SANICAP (bitfield in id-ctrl; NVMe 2.0 bit order: NVMe 1.4 swapped the
crypto/overwrite bits, which the SPEC documents as a hardware checklist
item, not something to guess at runtime):
  bit 0  block erase supported
  bit 1  crypto erase supported
  bit 2  overwrite supported

SANACT (sanitize command action):
  0  abort a running sanitize
  1  crypto erase
  2  block erase        (the paranoid plan's choice)
  3  overwrite

SSTAT (sanitize status log), bits [2:0] = state of the most recent sanitize:
  0b000  never sanitized
  0b001  completed successfully
  0b010  completed unsuccessfully
  0b011  in progress
bit 8 set means the sanitize actually modified the media (global data
erasure happened): 0x101 = success + media modified, the value a healthy
block-erase leaves behind. SPROG is a fraction with denominator 65536.
"""

from __future__ import annotations

import re

from .tools import ToolResult

_SANITIZE_ACTIONS = {
    "abort": 0,
    "crypto-erase": 1,
    "block-erase": 2,
    "overwrite": 3,
}


def sanitize_command(device: str, action: str, *, no_dealloc: bool = False) -> list[str]:
    """nvme sanitize argv for one action. `abort` maps to SANACT 0."""
    try:
        sanact = _SANITIZE_ACTIONS[action]
    except KeyError:
        raise ValueError(f"unknown sanitize action {action!r}") from None
    argv = ["nvme", "sanitize", device, f"--sanact={sanact}"]
    if no_dealloc:
        argv.append("--no-dealloc")
    return argv


def sanitize_log_command(device: str) -> list[str]:
    return ["nvme", "sanitize-log", device]


def id_ctrl_command(device: str) -> list[str]:
    return ["nvme", "id-ctrl", device]


def nvme_list_command() -> list[str]:
    return ["nvme", "list", "--output-format=json"]


# -- sanicap -------------------------------------------------------------------


class SanitizeCaps:
    __slots__ = ("block_erase", "crypto_erase", "overwrite")

    def __init__(self, raw: int = 0):
        self.block_erase = bool(raw & 0b001)
        self.crypto_erase = bool(raw & 0b010)
        self.overwrite = bool(raw & 0b100)

    @property
    def supported(self) -> bool:
        return self.block_erase or self.crypto_erase or self.overwrite

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SanitizeCaps(block_erase={self.block_erase}, "
            f"crypto_erase={self.crypto_erase}, overwrite={self.overwrite})"
        )


def parse_sanicap(value: int) -> SanitizeCaps:
    return SanitizeCaps(value & 0b111)


# nvme-cli prints the field in several shapes across versions:
#   "sanicap : 3"                     (modern, key : value)
#   "Sanitize Capabilities (SANCAP) : 0x3"   (verbose -H style)
_SANICAP_RE = re.compile(
    r"^\s*(?:sanicap|sanitize capabilities(?:\s*\(sancap\))?)\s*:\s*"
    r"(?:(\d+)|0x([0-9a-fA-F]+))\s*$",
    re.IGNORECASE,
)


def parse_sanicap_from_id_ctrl(text: str) -> SanitizeCaps:
    """Extract sanicap from `nvme id-ctrl` text output. Missing field
    means no sanitize support reported."""
    for line in text.splitlines():
        m = _SANICAP_RE.match(line)
        if m:
            raw = int(m.group(1) or m.group(2), 16 if m.group(2) else 10)
            return parse_sanicap(raw)
    return SanitizeCaps(0)


# -- sanitize status log ---------------------------------------------------------


class SanitizeLog:
    __slots__ = ("sprog", "sstat")

    def __init__(self, sprog: int, sstat: int):
        self.sprog = sprog
        self.sstat = sstat

    @property
    def percent(self) -> float:
        return self.sprog * 100.0 / 65535.0

    @property
    def state(self) -> str:
        """One of: never / success / failed / in-progress."""
        return {
            0b000: "never",
            0b001: "success",
            0b010: "failed",
            0b011: "in-progress",
        }.get(self.sstat & 0b111, "unknown")

    @property
    def media_modified(self) -> bool:
        return bool(self.sstat & 0x100)  # bit 8: global data erasure

    @property
    def done(self) -> bool:
        """Poll loop terminator: the state word says the operation ended.
        (sprog==65535 is the belt to this braces: some firmware parks the
        progress register at 100% before flipping sstat.)"""
        return self.state in ("success", "failed", "never") or self.sprog >= 65535

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"SanitizeLog(sprog={self.sprog}, sstat=0x{self.sstat:x}, state={self.state})"


# key shapes across nvme-cli versions:
#   "sprog : 12345"   "SSTAT : 0x101"
#   "Sanitize Progress (SPROG) : 65535"   "Sanitize Status (SSTAT) : 0x101"
_SPROG_RE = re.compile(
    r"^\s*(?:sprog|sanitize progress(?:\s*\(sprog\))?)\s*:\s*(\d+)\s*$",
    re.IGNORECASE,
)
_SSTAT_RE = re.compile(
    r"^\s*(?:sstat|sanitize status(?:\s*\(sstat\))?)\s*:\s*(?:0x)?([0-9a-fA-F]+)\s*$",
    re.IGNORECASE,
)


def parse_sanitize_log(text: str) -> SanitizeLog:
    sprog = sstat = 0
    for line in text.splitlines():
        m = _SPROG_RE.match(line)
        if m:
            sprog = int(m.group(1))
            continue
        m = _SSTAT_RE.match(line)
        if m:
            # sstat is printed in hex (with or without the 0x, both stripped
            # by the regex); nvme-cli has never printed it decimal
            sstat = int(m.group(1), 16)
    return SanitizeLog(sprog=sprog, sstat=sstat)


def parse_nvme_list_json(text: str) -> list[dict]:
    """Parse `nvme list -o json`: {"Devices":[{"DevicePath":..., "ModelNumber":
    ..., ...}]}. Returns [] for anything unparsable: enumeration must never
    raise."""
    import json

    try:
        data = json.loads(text)
    except ValueError:
        return []
    devices = data.get("Devices") if isinstance(data, dict) else None
    if not isinstance(devices, list):
        return []
    return [d for d in devices if isinstance(d, dict)]


def result_or_none(result: ToolResult) -> str | None:
    """stdout when the command succeeded, else None."""
    return result.stdout if result.ok else None
