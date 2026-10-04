"""worker_spawn.py: launch the root wipe worker through pkexec, and read
its JSONL event stream.

Why `pkexec env ...`: pkexec scrubs the environment, so anything the child
needs rides argv or an explicit `env` invocation. In an AppImage the
runtime FUSE-mounts as the invoking user: a root re-exec fails before
AppRun ever runs: so the frozen form sets APPIMAGE_EXTRACT_AND_RUN=1,
which makes the runtime extract to a temp dir instead of mounting. The
extract costs seconds; a wipe costs hours.

Cancellation is a control file, not a signal: the GUI (unprivileged) cannot
signal a root process, but both sides can see a file in a directory the
GUI created with 0700.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .wipe.protocol import read_events


def worker_argv(config_path: Path) -> list[str]:
    """The pkexec command line that re-executes this app as the wipe worker."""
    exe = sys.executable
    frozen = bool(getattr(sys, "frozen", False))
    prefix = ["pkexec", "env"]
    if os.environ.get("APPIMAGE"):
        prefix.append("APPIMAGE_EXTRACT_AND_RUN=1")
    if frozen:
        return [*prefix, exe, "--wipe-worker", "--config", str(config_path)]
    src = Path(__file__).resolve().parents[1]
    return [
        *prefix,
        f"PYTHONPATH={src}",
        exe,
        "-m",
        "chafftafarian",
        "--wipe-worker",
        "--config",
        str(config_path),
    ]


def new_control_dir() -> Path:
    """0700 dir the GUI owns; the worker watches <dir>/cancel, the config
    lives at <dir>/wipe.json."""
    d = Path(tempfile.mkdtemp(prefix="chafftafarian-wipe-"))
    os.chmod(d, 0o700)
    return d


class WipeWorkerProc(QObject):
    """One worker at a time. Mirrors the single-flight discipline of the
    chaff adapter: the bridge exposes one `busy`."""

    # NOT `event`: that name collides with QObject.event()
    workerEvent = Signal(str)  # one JSON event per line
    finished = Signal(int)  # worker exit code

    def __init__(self, config: dict, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.control_dir = new_control_dir()
        config_path = self.control_dir / "wipe.json"
        config_path.write_text(json.dumps(config))
        self._proc: subprocess.Popen | None = None
        self._cancel_sent = False

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        config_path = self.control_dir / "wipe.json"
        self._proc = subprocess.Popen(
            worker_argv(config_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # worker tracebacks ride the stream
            text=True,
            cwd="/",
        )
        threading.Thread(target=self._read, daemon=True, name="wipe-worker-reader").start()

    @property
    def cancel_sent(self) -> bool:
        return self._cancel_sent

    def request_cancel(self) -> bool:
        """Touch the cancel file. Returns False when there is nothing to
        cancel or a cancel was already sent."""
        if self._proc is None or self._proc.poll() is not None or self._cancel_sent:
            return False
        (self.control_dir / "cancel").write_text("cancel")
        self._cancel_sent = True
        return True

    def is_busy(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def shutdown(self) -> None:
        """App-exit path: request cancel and give the worker a grace period."""
        proc = self._proc
        if proc is not None and proc.poll() is None:
            self.request_cancel()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.terminate()

    # -- reader ----------------------------------------------------------------

    def _read(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        for event in read_events(proc.stdout):
            self.workerEvent.emit(json.dumps(event, default=str))
        rc = proc.wait()
        self.finished.emit(rc)
        # the control dir served its purpose once the worker is gone
        try:
            (self.control_dir / "wipe.json").unlink(missing_ok=True)
            (self.control_dir / "cancel").unlink(missing_ok=True)
            self.control_dir.rmdir()
        except OSError:
            pass  # leftover temp dir is harmless
