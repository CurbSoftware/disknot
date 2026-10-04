"""smart.py: parse a smartctl JSON snapshot for the DRIVES inspect panel.

Inspect only. No self-tests, no attribute writes. Missing tools and
permission errors are results, not exceptions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .tools import ToolResult, ToolRunner


@dataclass
class SmartSnapshot:
    passed: bool | None = None
    temperature_c: int | None = None
    power_on_hours: int | None = None
    firmware: str | None = None
    percentage_used: int | None = None
    available_spare: int | None = None
    media_errors: int | None = None

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "temperature_c": self.temperature_c,
            "power_on_hours": self.power_on_hours,
            "firmware": self.firmware,
            "percentage_used": self.percentage_used,
            "available_spare": self.available_spare,
            "media_errors": self.media_errors,
        }


def _intish(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def parse_smartctl_json(text: str) -> SmartSnapshot | None:
    """Return a snapshot from smartctl JSON, or None if nothing usable."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None

    snap = SmartSnapshot()
    status = data.get("smart_status")
    if isinstance(status, dict) and "passed" in status:
        snap.passed = bool(status["passed"])

    temp = data.get("temperature")
    if isinstance(temp, dict):
        snap.temperature_c = _intish(temp.get("current"))

    pot = data.get("power_on_time")
    if isinstance(pot, dict):
        snap.power_on_hours = _intish(pot.get("hours"))

    fw = data.get("firmware_version")
    if fw not in (None, ""):
        snap.firmware = str(fw)

    nvme = data.get("nvme_smart_health_information_log")
    if isinstance(nvme, dict):
        snap.percentage_used = _intish(nvme.get("percentage_used"))
        snap.available_spare = _intish(nvme.get("available_spare"))
        snap.media_errors = _intish(nvme.get("media_errors"))
        if snap.temperature_c is None:
            snap.temperature_c = _intish(nvme.get("temperature"))

    if (
        snap.passed is None
        and snap.temperature_c is None
        and snap.power_on_hours is None
        and snap.percentage_used is None
        and snap.available_spare is None
        and snap.media_errors is None
        and snap.firmware is None
    ):
        return None
    return snap


def _looks_like_permission(result: ToolResult, data: dict | None) -> bool:
    blob = f"{result.stderr}\n{result.stdout}".lower()
    if "permission denied" in blob:
        return True
    if data is None:
        return False
    messages = data.get("messages")
    if not isinstance(messages, list):
        return False
    for msg in messages:
        text = msg.get("string") if isinstance(msg, dict) else msg
        if isinstance(text, str) and "permission denied" in text.lower():
            return True
    return False


def probe_smart(runner: ToolRunner, path: str) -> tuple[dict | None, str | None]:
    """Run smartctl. Returns (snapshot_dict, error_key).

    error_key is missing | permission | failed, or None on success.
    """
    result = runner.run(["smartctl", "-j", "-H", "-A", "-i", path], timeout=20)
    if result.rc == 127 or "command not found" in result.stderr:
        return None, "missing"

    data: dict | None
    try:
        parsed = json.loads(result.stdout) if result.stdout.strip() else None
        data = parsed if isinstance(parsed, dict) else None
    except ValueError:
        data = None

    if _looks_like_permission(result, data):
        return None, "permission"

    snap = parse_smartctl_json(result.stdout)
    if snap is None:
        return None, "failed"
    return snap.as_dict(), None
