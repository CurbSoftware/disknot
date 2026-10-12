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
import shlex
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .wipe.protocol import read_events


def _outside_mount(path: Path) -> bool:
    text = str(path)
    return "/.mount_" not in text and not text.startswith("/tmp/.mount")


def real_appimage_path(env: dict | None = None, argv0: str | None = None) -> str | None:
    """The AppImage file on a normal filesystem, not the FUSE mount."""
    env = os.environ if env is None else env
    candidates: list[str] = []
    for key in ("APPIMAGE", "ARGV0"):
        value = env.get(key)
        if value:
            candidates.append(value)
    if argv0:
        candidates.append(argv0)
    owd = env.get("OWD") or env.get("PWD") or os.getcwd()
    for raw in candidates:
        if not raw or raw.startswith("-"):
            continue
        path = Path(raw)
        if not path.is_absolute():
            path = Path(owd) / path
        try:
            path = path.resolve()
        except OSError:
            continue
        if not _outside_mount(path) or not path.is_file():
            continue
        return str(path)
    return None


def relaunch_argv(env: dict | None = None, argv0: str | None = None) -> list[str]:
    """pkexec command that prompts, then starts this app as root.

    The elevated AppImage is started from the real file and detached, so
    it keeps running after this process exits. A path inside /tmp/.mount_*
    is never used.
    """
    env = os.environ if env is None else env
    pkexec = "/usr/bin/pkexec" if Path("/usr/bin/pkexec").is_file() else "pkexec"
    target = real_appimage_path(env, argv0)
    if target:
        quoted = shlex.quote(target)
        script = "\n".join([
            "set -eu",
            "log=$(mktemp /tmp/disknot-root.XXXXXX.log)",
            f"/usr/bin/env APPIMAGE_EXTRACT_AND_RUN=1 setsid {quoted} </dev/null >\"$log\" 2>&1 &",
            "pid=$!",
            "sleep 2",
            "if kill -0 \"$pid\" 2>/dev/null; then",
            "  exit 0",
            "fi",
            "echo \"elevated Disknot exited before the window opened\" >&2",
            "cat \"$log\" >&2 || true",
            "exit 1",
        ])
        return [pkexec, "/bin/sh", "-c", script]
    exe = sys.executable
    frozen = bool(getattr(sys, "frozen", False))
    if frozen and _outside_mount(Path(exe)):
        return [pkexec, exe]
    src = Path(__file__).resolve().parents[1]
    return [pkexec, "/usr/bin/env", f"PYTHONPATH={src}", exe, "-m", "chafftafarian"]


def relaunch_as_root() -> tuple[bool, str]:
    """Block until polkit finishes. Do not treat a still-open dialog as success."""
    if os.geteuid() == 0:
        return True, ""
    argv = relaunch_argv()
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        return False, str(exc)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if not detail:
            detail = f"pkexec exited {proc.returncode} with no message"
        return False, detail
    return True, ""


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
