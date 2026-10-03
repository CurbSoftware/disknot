"""writer.py: the dd replacement: pattern writes with real progress,
between-block cancellation, and sampled read-back verification.

Why not shell out to `dd status=progress`: parsing a child's stderr for
human-formatted byte counts is fragile, cancellation would need signal
tricks against a root process, and dd cannot verify. A Python writer with
1 MiB blocks preserves dd's semantics (whole device, direct I/O when the
kernel accepts it, fsync at the end, ENOSPC on the final partial block is
normal) and gives the orchestrator exact bytes-written and a cancel
checkpoint per block.
"""

from __future__ import annotations

import errno
import mmap
import os
import random
from collections.abc import Callable
from dataclasses import dataclass

BLOCK_SIZE = 1024 * 1024  # 1 MiB, dd's bs=1M
_PATTERNS = {"zeros": b"\x00", "ones": b"\xff"}


class WipeCancelled(Exception):
    """Raised from write_pattern/verify_pattern when should_cancel() says so."""


@dataclass(frozen=True)
class WriteStats:
    bytes_written: int
    blocks: int
    used_direct: bool
    enospc_at_end: bool


def _make_block(pattern: str) -> bytes:
    return _PATTERNS[pattern] * BLOCK_SIZE


def _make_block_direct(pattern: str) -> mmap.mmap:
    """A page-aligned block for O_DIRECT: mmap an anonymous 2 MiB region
    (page-aligned by construction) and fill 1 MiB of it."""
    buf = mmap.mmap(-1, BLOCK_SIZE)
    buf.write(_make_block(pattern))
    return buf


def write_pattern(
    path: str,
    pattern: str,
    *,
    total_bytes: int,
    on_progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    sync_every_blocks: int = 512,
) -> WriteStats:
    """Write `pattern` over [0, total_bytes) of `path` in 1 MiB blocks.

    Tries O_DIRECT with an mmap'd, page-aligned buffer (kernel/FS may
    refuse with EINVAL (zfs, some partitions), so we fall back to
    buffered writes with periodic fdatasync). The final ENOSPC when the
    device ends mid-block is normal and reported, not raised.
    """
    if pattern not in _PATTERNS:
        raise ValueError(f"unknown pattern {pattern!r}")

    def _cancelled() -> bool:
        return bool(should_cancel and should_cancel())

    written = 0
    blocks = 0
    enospc = False

    for direct in (True, False):
        flags = os.O_WRONLY
        if direct:
            flags |= getattr(os, "O_DIRECT", 0)
        fd = os.open(path, flags)
        try:
            if direct:
                buf = _make_block_direct(pattern)
                view = memoryview(buf)
            else:
                block = _make_block(pattern)
            while written < total_bytes:
                if _cancelled():
                    raise WipeCancelled(f"cancelled after {written} bytes")
                n = min(BLOCK_SIZE, total_bytes - written)
                try:
                    if direct:
                        os.write(fd, view[:n])
                    else:
                        os.write(fd, block[:n])
                except OSError as exc:
                    if (
                        exc.errno == errno.ENOSPC
                        and written > 0
                        and total_bytes - written < BLOCK_SIZE
                    ):
                        enospc = True
                        break  # the final partial block cannot fit: normal
                    raise
                written += n
                blocks += 1
                if not direct and blocks % sync_every_blocks == 0:
                    os.fdatasync(fd)
                if on_progress and (blocks % 16 == 0 or written >= total_bytes):
                    on_progress(written, total_bytes)
            os.fsync(fd)
            return WriteStats(
                bytes_written=written, blocks=blocks, used_direct=direct, enospc_at_end=enospc
            )
        except OSError as exc:
            if direct and exc.errno in (errno.EINVAL, errno.ENOTTY):
                # kernel refused O_DIRECT on this open or write: retry
                # buffered from the top
                written, blocks, enospc = 0, 0, False
                continue
            raise
        finally:
            os.close(fd)
    raise OSError("unreachable: both direct and buffered writes failed to start")


def _read_at(fd: int, offset: int, size: int) -> bytes:
    got = os.pread(fd, size, offset)
    if len(got) != size:
        raise OSError(f"short read at {offset}: {len(got)} of {size}")
    return got


def verify_pattern(
    path: str,
    pattern: str,
    *,
    total_bytes: int,
    sample_count: int = 64,
    seed: int = 0,
    block_size: int = 4096,
    on_progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[bool, list[dict]]:
    """Sampled read-back: first, middle, and last blocks plus `sample_count`
    deterministic random blocks must all equal the pattern. Returns
    (all_match, mismatches) where mismatches carry offset/expected/actual
    prefixes."""
    if pattern not in _PATTERNS:
        raise ValueError(f"unknown pattern {pattern!r}")
    byte = _PATTERNS[pattern]
    max_offset = max(0, total_bytes - block_size)
    # deterministic sample: first / middle / last blocks + random blocks,
    # so the same seed re-verifies the same sample
    rng = random.Random(f"chafftafarian-verify:{seed}")
    randoms = (
        []
        if max_offset <= 0
        else [
            (rng.randrange(0, max_offset + 1) // block_size) * block_size
            for _ in range(sample_count)
        ]
    )
    offsets = sorted(
        {
            0,
            (max_offset // 2 // block_size) * block_size,
            (max_offset // block_size) * block_size,
            *randoms,
        }
    )
    mismatches: list[dict] = []
    fd = os.open(path, os.O_RDONLY)
    try:
        for i, off in enumerate(offsets):
            if should_cancel and should_cancel():
                raise WipeCancelled(f"cancelled during verify at offset {off}")
            data = _read_at(fd, off, block_size)
            if data.count(byte) != block_size:
                mismatches.append(
                    {
                        "offset": off,
                        "expected": pattern,
                        "actual_prefix": data[:16].hex(),
                    }
                )
            if on_progress:
                on_progress(i + 1, len(offsets))
    finally:
        os.close(fd)
    return (not mismatches), mismatches
