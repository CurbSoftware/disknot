"""worker.py — the pkexec entry. Runs as root, headless, Qt-free.

Protocol: `--config PATH` points at a JSON file the GUI wrote; events flow
as JSONL on stdout (see protocol.py); exit 0 for completed or cleanly
cancelled, 1 for anything that failed or refused. The worker re-validates
the config, re-resolves the device, and re-runs the safety gate — it never
trusts the unprivileged side. The config file and its directory are
deleted once read (the control_dir survives for cancel signalling until
the GUI tears it down).
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

from .orchestrator import WipeConfig, WipeOrchestrator
from .protocol import config_required_keys, emit_event
from .tools import SubprocessToolRunner


def load_config(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config root must be an object")
    missing = [k for k in config_required_keys() if k not in data]
    if missing:
        raise ValueError(f"config missing keys: {', '.join(missing)}")
    return data


def main(config_path: Path) -> int:
    try:
        raw = load_config(Path(config_path))
    except (OSError, ValueError) as exc:
        # no stdout contract is possible without a config; stderr only
        print(f"wipe-worker: bad config: {exc}", file=sys.stderr)
        return 2

    try:
        # best effort: the config file served its purpose
        Path(config_path).unlink(missing_ok=True)

        cfg = WipeConfig.from_dict(raw)
        emit_event("hello", pid=os.getpid(), device=cfg.device, uid=os.getuid())
        if os.getuid() != 0:
            emit_event("error", text="not running as root — invoke via pkexec")
            emit_event("done", ok=False, cancelled=False)
            return 1

        summary = WipeOrchestrator(cfg, SubprocessToolRunner(), emit_event).run()
        emit_event(
            "done",
            ok=summary.ok,
            cancelled=summary.cancelled,
            device=summary.device,
            passes_total=summary.passes_total,
            passes_done=summary.passes_done,
            verify_ok=summary.verify_ok,
            verify_mismatches=summary.verify_mismatches,
            warnings=summary.warnings,
            errors=summary.errors,
            duration_s=round(summary.duration_s, 2),
        )
        return 0  # completed or cleanly cancelled
    except Exception:
        traceback.print_exc(file=sys.stderr)
        try:
            emit_event("error", text="worker crashed — see journal/stderr")
            emit_event("done", ok=False, cancelled=False)
        except Exception:
            pass
        return 1
