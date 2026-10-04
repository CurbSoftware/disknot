"""chaff_adapter.py: ChaffEngine on a QThread, speaking the bridge.

The vendored core publishes frozen-dataclass events through a callback on
the worker thread (chaff_generator/core/events.py). This adapter re-emits
them as Qt signals (queued connections deliver them to the GUI thread)
in the two shapes the frontend consumes:

  chaff_event(str)  JSON object per event      -> lineReady("chaff", ...)
  log_line(str)     sparse human-readable line -> lineReady("chaff.log", ...)
  finished_run(str) final result summary       -> reportReady("chaff.summary", ...)

Pattern lifted from chaff_generator/gui/workers/generation.py (upstream),
reshaped onto the Control Room signal trio.
"""

from __future__ import annotations

import json
import traceback
from dataclasses import asdict, is_dataclass
from datetime import datetime

from PySide6.QtCore import QObject, Signal

from chaff_generator.core.engine import ChaffEngine
from chaff_generator.core.errors import ChaffError
from chaff_generator.core.events import (
    FILE_EVENT_MIN_BYTES,
    FileCompleted,
    ProgressUpdated,
    RunCompleted,
    RunStarted,
    WarningRaised,
)
from chaff_generator.core.models import GenerationConfig, PreflightSummary

# Progress events already arrive throttled by the engine (4 Hz); JSON
# lines at that rate are cheap for the bridge.
_QUIET_EVENTS = (ProgressUpdated,)  # structured-only: no human log line


def event_to_json(event: object) -> str:
    def _default(v: object) -> object:
        if is_dataclass(v):
            return asdict(v)  # type: ignore[arg-type]  # narrowed by is_dataclass
        return str(v)

    if not is_dataclass(event) or isinstance(event, type):
        raise TypeError(f"not an event dataclass: {type(event).__name__}")
    data: dict = asdict(event)
    data["event"] = type(event).__name__
    return json.dumps(data, ensure_ascii=False, default=_default)


class ChaffWorker(QObject):
    """Owns one ChaffEngine; moved to a QThread by the Api."""

    chaff_event = Signal(str)
    log_line = Signal(str)
    finished_run = Signal(str)
    failed = Signal(str)

    def __init__(self, config: GenerationConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._engine = ChaffEngine(config, event_callback=self._on_event)

    # -- control (thread-safe: flags on the engine) --------------------------

    def pause(self) -> None:
        self._engine.pause()

    def resume(self) -> None:
        self._engine.resume()

    def cancel(self) -> None:
        self._engine.cancel()

    # -- the job --------------------------------------------------------------

    def run(self) -> None:  # pragma: no cover - exercised via the Qt event loop
        try:
            result = self._engine.generate()
        except ChaffError as exc:
            self.failed.emit(str(exc))
            return
        except OSError as exc:
            self.failed.emit(f"Filesystem error: {exc}")
            return
        except Exception:
            traceback.print_exc()
            self.failed.emit("generation failed (see stderr)")
            return
        from dataclasses import asdict as _asdict

        self.finished_run.emit(json.dumps(_asdict(result), default=str))

    def _on_event(self, event: object) -> None:
        self.chaff_event.emit(event_to_json(event))
        if isinstance(event, _QUIET_EVENTS):
            return
        if isinstance(event, RunStarted):
            self.log_line.emit(
                f"[{_now()}] [START] run {event.run_id} target "
                f"{_fmt_bytes(event.target_bytes)} free {_fmt_bytes(event.free_bytes)}"
            )
        elif isinstance(event, FileCompleted):
            if event.size >= FILE_EVENT_MIN_BYTES:  # keep the log readable
                self.log_line.emit(
                    f"[{_now()}] [FILE] {event.index}: {event.relative_path} "
                    f"({_fmt_bytes(event.size)})"
                )
        elif isinstance(event, WarningRaised):
            self.log_line.emit(
                f"[{_now()}] [WARN] {event.message}"
                + (f": {event.details}" if event.details else "")
            )
        elif isinstance(event, RunCompleted):
            status = event.result.status.value if event.result else "unknown"
            self.log_line.emit(f"[{_now()}] [DONE] status={status}")


def preflight_sync(config: GenerationConfig) -> PreflightSummary:
    """Cheap preflight, safe on the GUI thread (stat + tiny probe write)."""
    return ChaffEngine(config).preflight()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fmt_bytes(n: int | None) -> str:
    if n is None:
        return "?"
    f = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if f < 1024 or unit == "TiB":
            return f"{f:.0f}{unit}" if unit == "B" else f"{f:.1f}{unit}"
        f /= 1024
    return f"{n}B"
