"""tools.py — the one seam every external command goes through.

Device code never calls subprocess directly: inventory, safety, and the
orchestrator all take a ToolRunner, so tests script behavior with
FakeToolRunner and CI never touches real hardware.

nvme id-ctrl / sanitize-log output varies across nvme-cli versions (verbose
"Sanitize Progress (SPROG)" vs lowercase "sprog :"); the parsers in nvme.py
accept both shapes.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ToolResult:
    rc: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


class ToolRunner(Protocol):
    def run(self, argv: list[str], timeout: float | None = None) -> ToolResult: ...


class SubprocessToolRunner:
    """The real runner. utf-8 with replacement: nvme-cli output is plain
    ASCII but device model strings occasionally carry odd bytes."""

    def run(self, argv: list[str], timeout: float | None = None) -> ToolResult:
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except FileNotFoundError:
            return ToolResult(rc=127, stdout="", stderr=f"{argv[0]}: command not found")
        except subprocess.TimeoutExpired:
            return ToolResult(rc=124, stdout="", stderr=f"{argv[0]}: timed out")
        return ToolResult(rc=proc.returncode, stdout=proc.stdout or "", stderr=proc.stderr or "")


class FakeToolRunner:
    """Scripted runner for tests. Responses are matched in order against
    call counts; a callable receives (argv) and returns a ToolResult."""

    def __init__(self, *responses: ToolResult | Callable[[list[str]], ToolResult]) -> None:
        self._responses = list(responses)
        self.calls: list[list[str]] = []

    def run(self, argv: list[str], timeout: float | None = None) -> ToolResult:
        self.calls.append(list(argv))
        if not self._responses:
            return ToolResult(0, "", "")
        resp = self._responses.pop(0)
        if callable(resp):
            return resp(list(argv))
        return resp

    # -- convenience builders used by tests --------------------------------

    @staticmethod
    def ok(stdout: str = "", stderr: str = "") -> ToolResult:
        return ToolResult(0, stdout, stderr)

    @staticmethod
    def err(stderr: str = "", rc: int = 1) -> ToolResult:
        return ToolResult(rc, "", stderr)
