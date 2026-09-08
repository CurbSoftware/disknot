"""python -m chafftafarian — GUI launcher, offscreen screenshot harness, and
the privileged wipe-worker entry.

    python -m chafftafarian                              # launch the app
    python -m chafftafarian --list-views
    python -m chafftafarian --screenshot all --out DIR [--size WxH]
    python -m chafftafarian --wipe-worker --config CFG   # root only, headless

Screenshot mode renders index.html#/VIEW offscreen, waits for the frontend's
viewReady(view) call (30s timeout), then grabs the widget to PNG. Exit code 0
only if every requested PNG was written, non-empty and not blank.

The wipe worker is dispatched BEFORE any Qt import: it runs as root via
pkexec, headless, and must never construct a QApplication.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication

    from .app import Window

READY_TIMEOUT_MS = 30_000
SETTLE_MS = 400
GRAB_TRIES = 8


def apply_env_safety() -> None:
    """Sandbox teardown must happen before QApplication exists."""
    frozen = getattr(sys, "frozen", False)
    appimage = bool(os.environ.get("APPIMAGE"))
    forced = os.environ.get("CHAFFTAFARIAN_DISABLE_SANDBOX") == "1"
    if frozen or appimage or forced:
        os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"


def _parse_views(spec: str) -> list[str] | None:
    from .api import VIEWS

    if spec == "all":
        return list(VIEWS)
    views = [v.strip() for v in spec.split(",") if v.strip()]
    bad = [v for v in views if v not in VIEWS]
    if bad or not views:
        print(
            f"unknown view(s): {', '.join(bad or ['<empty>'])}; "
            f"valid: {', '.join(VIEWS)} (or 'all')",
            file=sys.stderr,
        )
        return None
    return views


def _parse_size(spec: str) -> tuple[int, int] | None:
    try:
        width_s, height_s = spec.lower().split("x")
        w, h = int(width_s), int(height_s)
        if w > 0 and h > 0:
            return w, h
    except ValueError:
        pass
    print(f"bad --size {spec!r}; expected WxH like 1600x1000", file=sys.stderr)
    return None


def _settle(app: QApplication, ms: int) -> None:
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def _grab(app: QApplication, view: Window) -> tuple[QPixmap | None, bool]:
    """grab() with blank-retry. Samples the CENTRAL view column, not the
    whole window: a page whose sidebar/status bar composited but whose main
    column didn't still has >2 colors overall and would pass a whole-window
    check while being effectively blank where it matters."""
    pix = None
    for _ in range(GRAB_TRIES):
        pix = view.grab()
        w, h = pix.width(), pix.height()
        # central band of the main column (sidebar ~240px left, status bar
        # ~44px bottom) — safely inside at any window size
        crop = pix.copy(
            int(w * 0.45),
            int(h * 0.25),
            int(w * 0.95) - int(w * 0.45),
            int(h * 0.75) - int(h * 0.25),
        )
        img = crop.scaled(32, 32).toImage()
        colors = set()
        for y in range(img.height()):
            for x in range(img.width()):
                colors.add(img.pixel(x, y))
        if len(colors) > 4:
            return pix, True
        _settle(app, SETTLE_MS)
    return pix, False


def run_screenshots(frontend: Path, views: list[str], out_dir: Path, size: tuple[int, int]) -> int:
    # Everything Chromium-related must be set before QApplication.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").split()
    for flag in ("--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"):
        if flag not in flags:
            flags.append(flag)
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(flags)
    for var in ("HOME", "XDG_RUNTIME_DIR"):
        if not os.environ.get(var):
            os.environ[var] = tempfile.mkdtemp(prefix=f"cf-{var}-")

    from PySide6.QtCore import QUrl

    from .app import create_app

    app, window = create_app(sys.argv[:1])
    w, h = size
    window.resize(w, h)
    window.show()

    failures: list[str] = []
    try:
        for view in views:
            url = QUrl.fromLocalFile(str(frontend))
            url.setFragment(f"/{view}")
            ok = _shoot(app, window, view, url, out_dir / f"{view}.png")
            if not ok:
                failures.append(view)
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        window.api.shutdown()
    if failures:
        print(f"screenshot FAILED for: {', '.join(failures)}", file=sys.stderr)
        return 1
    print(f"wrote {len(views)} screenshot(s) to {out_dir}")
    return 0


def _shoot(
    app: QApplication,
    window: Window,
    view: str,
    url: QUrl,
    png: Path,
) -> bool:
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    got: list[str] = []

    def on_ready(v: str) -> None:
        if v == view and not got:
            got.append(v)
            loop.quit()

    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    window.api.viewRendered.connect(on_ready)
    try:
        timer.start(READY_TIMEOUT_MS)
        window.load(url)
        loop.exec()
    finally:
        timer.stop()
        window.api.viewRendered.disconnect(on_ready)

    if not got:
        print(
            f"screenshot: viewReady({view!r}) never arrived within {READY_TIMEOUT_MS // 1000}s",
            file=sys.stderr,
        )
        return False

    # let the view-reveal animations finish before the first grab
    _settle(app, 700)
    pix, nonblank = _grab(app, window)
    if pix is None:
        return False
    png.parent.mkdir(parents=True, exist_ok=True)
    saved = pix.save(str(png), "PNG")
    if not saved or not png.exists() or png.stat().st_size == 0:
        print(f"screenshot: failed to write {png}", file=sys.stderr)
        return False
    if not nonblank:
        print(
            f"screenshot: {png} saved but looked blank (page may not have composited)",
            file=sys.stderr,
        )
        return False
    return True


def _selftest_wipe_plan() -> int:
    """Packaging smoke: the frozen app must be able to build the paranoid
    plan (proves the wipe package and chaff-free imports survived freezing
    — PyInstaller's favorite failure). No devices, no root."""
    from .wipe.inventory import DeviceInfo
    from .wipe.nvme import parse_sanicap, parse_sanitize_log
    from .wipe.plan import build_plan

    dev = DeviceInfo(
        kname="nvme9n1",
        path="/dev/nvme9n1",
        type="disk",
        size_bytes=1024**4,
        model="Selftest",
        serial="ST",
        transport="nvme",
    )
    caps = parse_sanicap(0b001)
    paranoid = build_plan(dev, caps)
    quick = build_plan(dev, caps, quick=True, verify=True)
    kinds = [p.kind for p in paranoid.passes]
    if kinds != ["sanitize", "zeros", "ones", "zeros", "sanitize", "zeros"]:
        print(f"SELFTEST FAIL: paranoid passes {kinds}", file=sys.stderr)
        return 1
    if [p.kind for p in quick.passes] != ["sanitize", "zeros", "verify"]:
        print("SELFTEST FAIL: quick passes", file=sys.stderr)
        return 1
    log = parse_sanitize_log("sprog : 65535\nsstat : 0x101")
    if log.state != "success" or not log.done:
        print("SELFTEST FAIL: sanitize log decode", file=sys.stderr)
        return 1
    print(f"SELFTEST: wipe plan OK ({len(paranoid.passes)} paranoid passes, decode OK)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chafftafarian", description="Chafftafarian — chaff, NVMe tools, sanitization"
    )
    parser.add_argument(
        "views",
        nargs="?",
        default=None,
        metavar="VIEW|all",
        help="screenshot mode: a view name, comma list, or 'all'",
    )
    parser.add_argument(
        "--screenshot",
        dest="views_flag",
        metavar="VIEW|all",
        help="same as the positional argument",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("screenshots"),
        help="output dir for PNGs (default: ./screenshots)",
    )
    parser.add_argument(
        "--size", default="1600x1000", metavar="WxH", help="viewport size (default: 1600x1000)"
    )
    parser.add_argument(
        "--list-views", action="store_true", help="print the screenshot views and exit"
    )
    parser.add_argument(
        "--selftest-wipe-plan",
        action="store_true",
        help="build a wipe plan from a synthetic device; "
        "exercises the wipe package without devices "
        "or root (packaging smoke)",
    )
    parser.add_argument(
        "--wipe-worker", action="store_true", help=argparse.SUPPRESS
    )  # internal: pkexec entry
    parser.add_argument(
        "--config", type=Path, default=None, help=argparse.SUPPRESS
    )  # worker config path
    args = parser.parse_args(argv)

    # Privileged headless worker: dispatched before ANY Qt import.
    if args.wipe_worker:
        if args.config is None:
            print("--wipe-worker requires --config", file=sys.stderr)
            return 2
        from .wipe.worker import main as worker_main

        return worker_main(args.config)

    if args.selftest_wipe_plan:
        return _selftest_wipe_plan()

    from .api import VIEWS

    if args.list_views:
        print("\n".join(VIEWS))
        return 0

    spec = args.views_flag or args.views
    apply_env_safety()

    if spec is None:
        from .app import run

        return run(sys.argv[:1])

    views = _parse_views(spec)
    if views is None:
        return 2
    size = _parse_size(args.size)
    if size is None:
        return 2
    from .app import resolve_frontend

    frontend = resolve_frontend()
    if frontend is None:
        print(
            "frontend index.html not found — set CHAFFTAFARIAN_FRONTEND "
            "or run from the repo checkout",
            file=sys.stderr,
        )
        return 2
    return run_screenshots(frontend, views, args.out, size)


if __name__ == "__main__":
    sys.exit(main())
