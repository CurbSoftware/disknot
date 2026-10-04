"""ata.py: parse hdparm identify / sanitize-status and build argv lists.

SATA firmware erase is opt-in behind allow_non_nvme. Frozen drives are
detected and refused for firmware methods; this module never thaws a
drive (no suspend, no SCSI delete/rescan). Password `p` matches the
hdparm convention used by super_drive_wipe.

Sanitize Device status tokens (hdparm --sanitize-status):
  SD0  completed successfully
  SD1  failed
  SD2  in progress
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .nvme import result_or_none
from .tools import ToolRunner

HDPARM_PASSWORD = "p"
_YES = "--yes-i-know-what-i-am-doing"

ATA_ACTIONS = ("sata-block-erase", "sata-crypto", "sata-secure-erase")


@dataclass
class AtaCaps:
    frozen: bool = False
    sanitize_block: bool = False
    sanitize_crypto: bool = False
    security_erase: bool = False
    security_erase_enhanced: bool = False

    @property
    def firmware_supported(self) -> bool:
        return self.sanitize_block or self.sanitize_crypto or self.security_erase

    def as_dict(self) -> dict:
        return {
            "frozen": self.frozen,
            "sanitize_block": self.sanitize_block,
            "sanitize_crypto": self.sanitize_crypto,
            "security_erase": self.security_erase,
            "security_erase_enhanced": self.security_erase_enhanced,
        }


@dataclass
class AtaSanitizeStatus:
    state: str  # in-progress | success | failed | unsupported | unknown
    raw: str = ""

    @property
    def done(self) -> bool:
        return self.state in ("success", "failed", "unsupported")


def identify_command(device: str) -> list[str]:
    return ["hdparm", "-I", device]


def sanitize_status_command(device: str) -> list[str]:
    return ["hdparm", "--sanitize-status", device]


def sanitize_block_erase_command(device: str) -> list[str]:
    return ["hdparm", _YES, "--sanitize-block-erase", device]


def sanitize_crypto_scramble_command(device: str) -> list[str]:
    return ["hdparm", _YES, "--sanitize-crypto-scramble", device]


def security_set_pass_command(device: str, password: str = HDPARM_PASSWORD) -> list[str]:
    return ["hdparm", "--user-master", "u", "--security-set-pass", password, device]


def security_erase_command(
    device: str, password: str = HDPARM_PASSWORD, *, enhanced: bool = False
) -> list[str]:
    flag = "--security-erase-enhanced" if enhanced else "--security-erase"
    return ["hdparm", "--user-master", "u", flag, password, device]


def security_disable_command(device: str, password: str = HDPARM_PASSWORD) -> list[str]:
    return ["hdparm", "--security-disable", password, device]


def _collapsed(line: str) -> str:
    return " ".join(line.lower().split())


def _has_positive(line: str, token: str) -> bool:
    """True when `token` appears and the line is not a 'not …' denial."""
    if token.lower() not in line.lower():
        return False
    return not re.search(r"\bnot\b", line, re.IGNORECASE)


def parse_hdparm_identify(text: str) -> AtaCaps:
    """Extract frozen / sanitize / security-erase flags from `hdparm -I`."""
    caps = AtaCaps()
    in_security = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        collapsed = _collapsed(line)
        if collapsed.startswith("security:"):
            in_security = True
            continue
        if in_security and collapsed.endswith(":") and not collapsed.startswith("supported"):
            in_security = False

        if "not frozen" in collapsed:
            caps.frozen = False
        elif re.search(r"\bfrozen\b", collapsed):
            caps.frozen = True

        if _has_positive(line, "BLOCK_ERASE"):
            caps.sanitize_block = True
        if _has_positive(line, "CRYPTO_SCRAMBLE"):
            caps.sanitize_crypto = True

        if in_security:
            if collapsed.startswith("not supported"):
                caps.security_erase = False
            elif collapsed == "supported":
                caps.security_erase = True
            if "supported: enhanced erase" in collapsed and not collapsed.startswith("not"):
                caps.security_erase = True
                caps.security_erase_enhanced = True
    return caps


def parse_sanitize_status(text: str) -> AtaSanitizeStatus:
    blob = text.upper()
    if "NOT SUPPORTED" in blob:
        return AtaSanitizeStatus("unsupported", text)
    if "SD2" in blob:
        return AtaSanitizeStatus("in-progress", text)
    if "SD0" in blob:
        return AtaSanitizeStatus("success", text)
    if "SD1" in blob:
        return AtaSanitizeStatus("failed", text)
    return AtaSanitizeStatus("unknown", text)


def probe_ata(runner: ToolRunner, path: str) -> AtaCaps | None:
    """None when hdparm is missing or identify fails."""
    raw = result_or_none(runner.run(identify_command(path), timeout=15))
    if not raw:
        return None
    return parse_hdparm_identify(raw)
