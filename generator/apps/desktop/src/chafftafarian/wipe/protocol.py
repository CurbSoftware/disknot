"""protocol.py: the JSONL event contract between the root wipe worker and
the GUI (and the mock, and the tests).

One JSON object per line on the worker's stdout, flushed per line. The
first line is always `hello`; the last is always `done`. Tracebacks go to
stderr: stdout carries nothing but events.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Iterator

EVENT_TYPES = (
    "hello",
    "log",
    "pass_start",
    "pass_progress",
    "sanitize_progress",
    "verify_progress",
    "warning",
    "error",
    "done",
)


def emit_event(event: str, **fields: object) -> None:
    """Write one event line to stdout, flushed (the GUI reads line-wise)."""
    payload = {"event": event, **fields}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def read_events(stream: Iterable[str]) -> Iterator[dict]:
    """Iterate parsed events from a JSONL stream (the GUI side)."""
    for line in stream:
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except ValueError:
            continue  # a torn line mid-crash: keep the events around it


# -- worker config (GUI -> worker, via a JSON file) ----------------------------


def config_required_keys() -> tuple[str, ...]:
    """The keys the worker refuses to run without. The worker re-validates
    everything; the GUI's word is not trusted."""
    return ("device", "passes", "confirmation_word", "confirmation_device", "control_dir")
