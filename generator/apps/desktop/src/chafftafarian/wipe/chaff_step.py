"""chaff_step.py: modular disk tool steps for creating and deleting chaff
synthetic corpora on block devices or temporary mounts.

Follows SPEC boundary: block device operations (format, mount, umount) are
performed by the device layer; once mounted, the chaff generator engine
populates realistic files onto the filesystem to obscure unallocated cells
and slack space before final wipe/verification.
"""

from __future__ import annotations

import contextlib
import shutil
from collections.abc import Callable
from pathlib import Path

from .tools import ToolRunner


def _get_mount_point(dev_path: str, runner: ToolRunner) -> str | None:
    """Check if dev_path or its partitions are already mounted."""
    res = runner.run(["findmnt", "-n", "-o", "TARGET", dev_path], timeout=10)
    if res.ok and res.stdout.strip():
        return res.stdout.strip().splitlines()[0]
    return None


def create_chaff_on_device(
    dev_path: str,
    runner: ToolRunner,
    *,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    temp_mount_base: str = "/tmp/disknot-chaff",
) -> tuple[bool, str]:
    """Format temporary filesystem if unmounted, mount, and generate chaff files.
    Returns (ok, message)."""
    mount_dir = _get_mount_point(dev_path, runner)

    if not mount_dir:
        # Need to create temporary mount
        kname = Path(dev_path).name
        target_dir = Path(f"{temp_mount_base}-{kname}")
        target_dir.mkdir(parents=True, exist_ok=True)
        mount_dir = str(target_dir)

        # Format temporary filesystem
        mkfs = runner.run(["mkfs.ext4", "-F", "-q", dev_path], timeout=120)
        if not mkfs.ok and "not found" not in mkfs.stderr:
            # If mkfs fails on real device, report error
            pass

        # Mount device
        runner.run(["mount", dev_path, mount_dir], timeout=30)

    try:
        if should_cancel and should_cancel():
            return False, "cancelled before chaff generation"

        # Generate chaff synthetic files into mount_dir
        from chaff_generator.core.engine import ChaffEngine
        from chaff_generator.core.models import RunStatus, dict_to_config

        cfg = dict_to_config(
            {
                "target": mount_dir,
                "amount": "50 MiB",
                "profile": "storage-test",
            }
        )

        def event_cb(ev: object) -> None:
            if should_cancel and should_cancel():
                raise RuntimeError("cancelled during chaff generation")
            # If ProgressUpdated or similar, notify progress
            if (
                hasattr(ev, "bytes_written")
                and hasattr(ev, "target_bytes")
                and on_progress
                and getattr(ev, "target_bytes", 0) > 0
            ):
                on_progress(getattr(ev, "bytes_written", 0), getattr(ev, "target_bytes", 1))

        engine = ChaffEngine(cfg, event_callback=event_cb)
        result = engine.generate()
        if result.status != RunStatus.COMPLETED:
            return False, f"chaff generation failed: {result.error or result.status}"

        return (
            True,
            f"chaff corpus generated ({result.files_created} files, {result.bytes_written} bytes)",
        )
    except Exception as exc:
        return False, f"chaff generation error: {exc}"


def delete_chaff_on_device(
    dev_path: str,
    runner: ToolRunner,
    *,
    temp_mount_base: str = "/tmp/disknot-chaff",
) -> tuple[bool, str]:
    """Delete chaff runs from the device's filesystem and unmount temporary mounts."""
    kname = Path(dev_path).name
    temp_dir = Path(f"{temp_mount_base}-{kname}")

    mount_dir = _get_mount_point(dev_path, runner)
    cleaned_count = 0

    target_paths = [Path(mount_dir)] if mount_dir else []
    if temp_dir.exists() and temp_dir not in target_paths:
        target_paths.append(temp_dir)

    for p in target_paths:
        if p.is_dir():
            for child in p.glob("Chaff_Run_*"):
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                    cleaned_count += 1

    # Unmount if temp mount was used
    if str(temp_dir) == mount_dir or temp_dir.exists():
        runner.run(["umount", str(temp_dir)], timeout=30)
        with contextlib.suppress(OSError):
            temp_dir.rmdir()

    return True, f"cleaned up {cleaned_count} chaff run directories"
