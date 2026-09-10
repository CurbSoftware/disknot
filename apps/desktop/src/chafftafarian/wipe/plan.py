"""plan.py: build the pass table before anything destructive happens.

The paranoid plan reproduces secure_wipe.sh v2.1.0's six passes exactly:
sanitize, zeros, ones, zeros, sanitize, zeros: sanitize passes dropped
(with a note) when the controller reports no sanitize support. Standard is
three passes (sanitize, zeros, ones). Quick is sanitize plus final zeros.
Verification is an optional trailing pass. Sanitize action is block-erase
by default; crypto-erase (SANACT=4) or overwrite (SANACT=3) when the
controller reports it and the user asked. Non-NVMe firmware erase uses
hdparm (sata-*) and is skipped when the drive is frozen.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ata import ATA_ACTIONS, AtaCaps
from .inventory import DeviceInfo
from .nvme import NVME_ACTIONS, SanitizeCaps

# Rough sequential-write estimate for ETA display only.
ESTIMATE_BPS = 200 * 1000 * 1000

PLANS = ("paranoid", "standard", "quick")
ACTIONS = NVME_ACTIONS

IN_PROGRESS_NOTE = (
    "A sanitize is already in progress. Starting this wipe will abort it first "
    "(sanact=0), then run this plan."
)
FROZEN_NOTE = (
    "This drive is frozen. Firmware erase is refused. Power-cycle the drive. "
    "This app will not suspend the machine to thaw it. Overwrite passes still run."
)


@dataclass(frozen=True)
class Pass:
    n: int
    kind: str  # sanitize | zeros | ones | verify
    detail: str
    est_s: int | None
    action: str | None = None  # NVMe or sata-* on sanitize passes


@dataclass
class WipePlan:
    device: str
    model: str | None
    size_bytes: int
    passes: list[Pass] = field(default_factory=list)
    sanitize_supported: bool = True
    blockers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "device": self.device,
            "model": self.model,
            "size_bytes": self.size_bytes,
            "passes": [
                {
                    "n": p.n,
                    "kind": p.kind,
                    "detail": p.detail,
                    "est_s": p.est_s,
                    "action": p.action,
                }
                for p in self.passes
            ],
            "sanitize_supported": self.sanitize_supported,
            "blockers": self.blockers,
            "notes": self.notes,
        }


def resolve_plan_name(plan: str | None, quick: bool) -> str:
    """`plan` wins when it is a known name; `quick` is the old bool flag."""
    if plan in PLANS:
        return plan
    return "quick" if quick else "paranoid"


def resolve_sanitize_action(caps: SanitizeCaps, requested: str | None) -> str | None:
    """Pick a sanitize method this controller can actually run."""
    want = requested if requested in ACTIONS else "block-erase"
    avail: list[str] = []
    if caps.block_erase:
        avail.append("block-erase")
    if caps.crypto_erase:
        avail.append("crypto-erase")
    if caps.overwrite:
        avail.append("overwrite")
    if want in avail:
        return want
    if "block-erase" in avail:
        return "block-erase"
    if "crypto-erase" in avail:
        return "crypto-erase"
    if "overwrite" in avail:
        return "overwrite"
    return None


def resolve_ata_action(ata: AtaCaps, requested: str | None) -> str | None:
    """Pick an hdparm firmware method. Never auto-picks security-erase.
    Frozen drives get no firmware method."""
    if ata.frozen:
        return None
    want = requested
    if want == "block-erase":
        want = "sata-block-erase"
    elif want == "crypto-erase":
        want = "sata-crypto"
    elif want == "overwrite":
        want = None
    if want not in ATA_ACTIONS:
        if ata.sanitize_block:
            return "sata-block-erase"
        if ata.sanitize_crypto:
            return "sata-crypto"
        return None
    if want == "sata-block-erase" and ata.sanitize_block:
        return want
    if want == "sata-crypto" and ata.sanitize_crypto:
        return want
    if want == "sata-secure-erase" and ata.security_erase:
        return want
    if ata.sanitize_block:
        return "sata-block-erase"
    if ata.sanitize_crypto:
        return "sata-crypto"
    return None


def _sanitize_detail(action: str, *, second: bool = False) -> str:
    which = "a second NVMe Sanitize" if second else "NVMe Sanitize"
    if action == "crypto-erase":
        return (
            f"This pass will run {which} (crypto erase). "
            "The controller destroys its media encryption key."
        )
    if action == "overwrite":
        return (
            f"This pass will run {which} (overwrite). "
            "The controller overwrites every block with its sanitize pattern."
        )
    if action == "sata-block-erase":
        which_ata = "a second ATA sanitize" if second else "ATA sanitize"
        return (
            f"This pass will run {which_ata} (block erase) via hdparm. "
            "The controller erases every block."
        )
    if action == "sata-crypto":
        which_ata = "a second ATA sanitize" if second else "ATA sanitize"
        return (
            f"This pass will run {which_ata} (crypto scramble) via hdparm. "
            "The controller destroys its media encryption key."
        )
    if action == "sata-secure-erase":
        which_ata = "a second ATA security erase" if second else "ATA security erase"
        return (
            f"This pass will run {which_ata} via hdparm. "
            "This sets a temporary password p, then erases. "
            "If it is interrupted, the drive may stay locked."
        )
    return f"This pass will run {which} (block erase). The controller erases every block."


def _fallback_notes(out: WipePlan, requested: str | None, action: str | None) -> None:
    want = requested if requested in ACTIONS else "block-erase"
    if requested in ATA_ACTIONS:
        if action != requested:
            if action:
                out.notes.append(
                    f"This drive does not support {requested.replace('-', ' ')}. "
                    f"Using {action.replace('-', ' ')} instead."
                )
            else:
                out.notes.append(
                    f"This drive does not support {requested.replace('-', ' ')}."
                )
        return
    if want == "crypto-erase" and action != "crypto-erase":
        if action == "block-erase":
            out.notes.append(
                "This drive does not support crypto erase. Using block erase instead."
            )
        elif action == "overwrite":
            out.notes.append(
                "This drive does not support crypto erase. Using overwrite instead."
            )
        else:
            out.notes.append("This drive does not support crypto erase.")
    elif want == "overwrite" and action != "overwrite":
        if action:
            out.notes.append(
                f"This drive does not support overwrite. "
                f"Using {action.replace('-', ' ')} instead."
            )
        else:
            out.notes.append("This drive does not support overwrite.")
    elif want == "block-erase" and action == "crypto-erase":
        out.notes.append(
            "This drive does not support block erase. Using crypto erase instead."
        )
    elif want == "block-erase" and action == "overwrite":
        out.notes.append(
            "This drive does not support block erase. Using overwrite instead."
        )


def build_plan(
    dev: DeviceInfo,
    caps: SanitizeCaps,
    *,
    verify: bool = False,
    quick: bool = False,
    plan: str | None = None,
    sanitize_action: str | None = None,
    ata: AtaCaps | None = None,
    last_sanitize_state: str | None = None,
) -> WipePlan:
    name = resolve_plan_name(plan, quick)
    out = WipePlan(
        device=dev.path,
        model=dev.model,
        size_bytes=dev.size_bytes,
        sanitize_supported=caps.supported,
    )
    write_est = int(dev.size_bytes / ESTIMATE_BPS) or 1
    skip_missing_notes = False

    if last_sanitize_state == "in-progress":
        out.notes.append(IN_PROGRESS_NOTE)

    if not dev.is_nvme:
        ata_caps = ata or AtaCaps()
        action = resolve_ata_action(ata_caps, sanitize_action)
        out.sanitize_supported = ata_caps.firmware_supported
        if ata_caps.frozen:
            out.notes.append(FROZEN_NOTE)
            skip_missing_notes = True
        else:
            _fallback_notes(out, sanitize_action, action)
        if action is None and not ata_caps.frozen:
            out.notes.append(
                "This drive has no firmware erase. Overwrite passes only. "
                "Remapped cells may retain data."
            )
    else:
        action = resolve_sanitize_action(caps, sanitize_action)
        _fallback_notes(out, sanitize_action, action)
        if action is None:
            out.notes.append(
                "This controller reports no sanitize support. "
                "Overwrite passes only. Remapped cells may retain data."
            )

    def add(kind: str, detail: str, est_s: int | None, act: str | None = None) -> None:
        out.passes.append(
            Pass(n=len(out.passes) + 1, kind=kind, detail=detail, est_s=est_s, action=act)
        )

    def add_sanitize(*, second: bool = False, note_if_missing: bool = False) -> None:
        if action is None:
            if note_if_missing and not skip_missing_notes:
                which = "second sanitize" if second else "sanitize"
                out.notes.append(f"This pass ({which}) skipped: not supported")
            return
        add("sanitize", _sanitize_detail(action, second=second), None, action)

    if name == "quick":
        add_sanitize()
        add("zeros", "This pass will overwrite the whole disk with zeros.", write_est)
    elif name == "standard":
        add_sanitize()
        add("zeros", "This pass will overwrite the whole disk with zeros.", write_est)
        add("ones", "This pass will overwrite the whole disk with 0xFF.", write_est)
    else:
        add_sanitize(note_if_missing=True)
        add("zeros", "This pass will overwrite the whole disk with zeros.", write_est)
        add("ones", "This pass will overwrite the whole disk with 0xFF.", write_est)
        add("zeros", "This pass will overwrite the whole disk with zeros again.", write_est)
        add_sanitize(second=True, note_if_missing=True)
        add(
            "zeros",
            "This pass will overwrite the whole disk with zeros. The drive is left zeroed.",
            write_est,
        )

    if verify:
        add(
            "verify",
            "This pass will read a sample of blocks and check they match zeros.",
            write_est // 8,
        )
    return out
