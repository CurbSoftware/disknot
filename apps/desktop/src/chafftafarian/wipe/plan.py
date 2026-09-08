"""plan.py: build the pass table before anything destructive happens.

The paranoid plan reproduces secure_wipe.sh v2.1.0's six passes exactly:
sanitize, zeros, ones, zeros, sanitize, zeros: sanitize passes dropped
(with a note) when the controller reports no sanitize support. A quick
plan (sanitize + final zeros) exists for sane re-use of already-wiped
drives; verification is an optional trailing pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .inventory import DeviceInfo
from .nvme import SanitizeCaps

# Rough sequential-write estimate for ETA display only.
ESTIMATE_BPS = 200 * 1000 * 1000


@dataclass(frozen=True)
class Pass:
    n: int
    kind: str  # sanitize | zeros | ones | verify
    detail: str
    est_s: int | None


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
                {"n": p.n, "kind": p.kind, "detail": p.detail, "est_s": p.est_s}
                for p in self.passes
            ],
            "sanitize_supported": self.sanitize_supported,
            "blockers": self.blockers,
            "notes": self.notes,
        }


def build_plan(
    dev: DeviceInfo, caps: SanitizeCaps, *, verify: bool = False, quick: bool = False
) -> WipePlan:
    plan = WipePlan(
        device=dev.path,
        model=dev.model,
        size_bytes=dev.size_bytes,
        sanitize_supported=caps.supported,
    )
    write_est = int(dev.size_bytes / ESTIMATE_BPS) or 1

    def add(kind: str, detail: str, est_s: int | None) -> None:
        plan.passes.append(Pass(n=len(plan.passes) + 1, kind=kind, detail=detail, est_s=est_s))

    if not caps.supported:
        plan.notes.append(
            "controller reports no sanitize support (sanicap=0): "
            "overwrite passes only: remapped cells may retain data"
        )

    if quick:
        if caps.supported:
            add("sanitize", "NVMe Sanitize (block erase)", None)
        add("zeros", "Overwrite with zeros", write_est)
        if verify:
            add("verify", "Sampled read-back verification", write_est // 8)
        return plan

    # paranoid six, bracketed like secure_wipe.sh
    if caps.supported:
        add("sanitize", "NVMe Sanitize: first hardware erase", None)
    else:
        plan.notes.append("pass 1 (sanitize) skipped: not supported")
    add("zeros", "Overwrite with zeros", write_est)
    add("ones", "Overwrite with 0xFF", write_est)
    add("zeros", "Overwrite with zeros again", write_est)
    if caps.supported:
        add("sanitize", "NVMe Sanitize: second hardware erase", None)
    else:
        plan.notes.append("pass 5 (sanitize) skipped: not supported")
    add("zeros", "Final zero pass: leaves the drive zeroed", write_est)
    if verify:
        add("verify", "Sampled read-back verification", write_est // 8)
    return plan
