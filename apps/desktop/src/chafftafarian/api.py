"""api.py: the QWebChannel bridge object.

Exactly one ``Api(QObject)`` is registered as "api" on the channel. Slots are
QString in / QString out, payloads JSON: the frozen contract is BRIDGE.md.
Slots run on the GUI thread; worker threads only ever emit signals.

Chafftafarian has no external engine process to drive: the vendored chaff
core runs in-process on a QThread (chaff_adapter), and device operations run
in a root worker spawned via pkexec (worker_spawn). This file is the single
surface both of those talk to.
"""

from __future__ import annotations

import json
import shutil
import threading
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QWidget

from chaff_generator.cleanup.manager import CleanupManager
from chaff_generator.core.errors import ChaffError
from chaff_generator.core.models import CompletionAction, dict_to_config
from chaff_generator.manifest.reader import discover_runs, read_manifest
from chaff_generator.manifest.verifier import (
    VerificationMode,
    verify_run,
)

from . import __version__, config
from .chaff_adapter import ChaffWorker, preflight_sync
from .tail import FileTail
from .wipe import inventory, safety
from .wipe import plan as wipe_plan_mod
from .wipe.nvme import parse_sanicap_from_id_ctrl, result_or_none
from .wipe.tools import SubprocessToolRunner
from .worker_spawn import WipeWorkerProc

# The screenshot-harness views; the frontend routes #/<view> to these.
VIEWS = ("drives", "sanitize", "chaff", "runs", "settings", "dispatch")

# External tools the app shells out to (directly or via the root worker).
TOOLS = ("nvme", "pkexec", "lsblk", "findmnt")


def _dumps(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


class Api(QObject):
    # Qt -> JS
    lineReady = Signal(str, str)
    reportReady = Signal(str, str)
    stateChanged = Signal(str)
    # internal: the screenshot harness waits on this
    viewRendered = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._app_settings: dict = config.load_app_settings()
        self._chaff_thread: QThread | None = None
        self._chaff_worker: ChaffWorker | None = None
        self._verify_busy = False
        self._wipe_proc: WipeWorkerProc | None = None
        self._runner = SubprocessToolRunner()
        self._tail: FileTail | None = None
        self._wipe_log: Path | None = None

    # ---------------------------------------------------------- util / core

    def _guard(
        self,
        fn: Callable[..., Any],
        *args: object,
        fallback: str = '{"ok": false, "error": "internal error"}',
    ) -> Any:
        """Never let a slot raise into JS; log to stderr instead."""
        try:
            return fn(*args)
        except Exception:
            traceback.print_exc()
            return fallback

    def _reload_settings(self) -> None:
        self._app_settings = config.load_app_settings()

    def _tools(self) -> dict[str, str | None]:
        return {name: shutil.which(name) for name in TOOLS}

    # -- busy state: one operation at a time, chaff or wipe ------------------

    def _chaff_busy(self) -> bool:
        return self._chaff_thread is not None and self._chaff_thread.isRunning()

    def _wipe_busy(self) -> bool:
        return self._wipe_proc is not None and self._wipe_proc.is_busy()

    def _busy(self) -> bool:
        return self._chaff_busy() or self._wipe_busy()

    def _kind(self) -> str | None:
        if self._chaff_busy():
            return "chaff"
        if self._wipe_busy():
            return "wipe"
        return None

    def _state(self) -> dict:
        return {
            "busy": self._busy(),
            "kind": self._kind(),
            "started": None,
            "core_version": __version__,
            "tools": self._tools(),
        }

    def _emit_state(self) -> None:
        self.stateChanged.emit(_dumps(self._state()))

    def shutdown(self) -> None:
        """Cancel background work on app exit, with a grace period."""
        if self._tail is not None:
            self._tail.stop()
        thread = self._chaff_thread
        if self._chaff_busy() and self._chaff_worker is not None and thread is not None:
            self._chaff_worker.cancel()
            thread.quit()
            thread.wait(5000)
        if self._wipe_proc is not None:
            self._wipe_proc.shutdown()

    # ================================================================ slots

    @Slot(result=str)
    def getBootstrap(self) -> str:
        return self._guard(self._bootstrap)

    def _bootstrap(self) -> str:
        out = {
            "core": {"version": __version__, "ready": True},
            "tools": self._tools(),
            "busy": self._busy(),
            "kind": self._kind(),
            "app_settings": {
                "allow_non_nvme": bool(self._app_settings.get("allow_non_nvme", False)),
                "default_target": self._app_settings.get("default_target", ""),
            },
        }
        return _dumps(out)

    # ------------------------------------------------------------- chaff

    @Slot(str, result=str)
    def chaffPreflight(self, config_json: str) -> str:
        return self._guard(self._chaff_preflight, config_json)

    def _chaff_preflight(self, config_json: str) -> str:
        cfg = dict_to_config(json.loads(config_json or "{}"))
        summary = preflight_sync(cfg)
        from dataclasses import asdict

        return _dumps({"ok": True, **asdict(summary)})

    @Slot(str, result=str)
    def chaffStart(self, config_json: str) -> str:
        return self._guard(self._chaff_start, config_json)

    def _chaff_start(self, config_json: str) -> str:
        if self._busy():
            return _dumps({"ok": False, "error": "busy"})
        try:
            cfg = dict_to_config(json.loads(config_json or "{}"))
        except ChaffError as exc:
            return _dumps({"ok": False, "error": f"invalid config: {exc}"})

        self._chaff_thread = QThread(self)
        self._chaff_worker = ChaffWorker(cfg)
        self._chaff_worker.moveToThread(self._chaff_thread)
        self._chaff_thread.started.connect(self._chaff_worker.run)
        self._chaff_worker.chaff_event.connect(lambda text: self.lineReady.emit("chaff", text))
        self._chaff_worker.log_line.connect(lambda text: self.lineReady.emit("chaff.log", text))
        self._chaff_worker.failed.connect(self._chaff_failed)
        self._chaff_worker.finished_run.connect(self._chaff_finished)

        # remember where runs live so the RUNS view finds them
        if str(cfg.target.path) != self._app_settings.get("default_target"):
            self._app_settings["default_target"] = str(cfg.target.path)
            config.save_app_settings(self._app_settings)

        self._chaff_thread.start()
        self._emit_state()
        return _dumps({"ok": True, "error": None})

    def _chaff_failed(self, message: str) -> None:
        self._teardown_chaff_thread()
        self.lineReady.emit("chaff.log", f"generation failed: {message}")
        self.reportReady.emit(
            "chaff.summary",
            _dumps({"ok": False, "cancelled": False, "status": "failed", "error": message}),
        )
        self._emit_state()

    def _chaff_finished(self, result_json: str) -> None:
        self._teardown_chaff_thread()
        data = json.loads(result_json)
        data["ok"] = data.get("status") == "completed"
        data["cancelled"] = data.get("status") == "cancelled"
        self.reportReady.emit("chaff.summary", _dumps(data))
        self._emit_state()

    def _teardown_chaff_thread(self) -> None:
        thread, self._chaff_thread = self._chaff_thread, None
        self._chaff_worker = None
        if thread is not None:
            thread.quit()
            thread.wait(5000)

    @Slot(result=str)
    def chaffCancel(self) -> str:
        return self._guard(self._chaff_control, "cancel")

    @Slot(result=str)
    def chaffPause(self) -> str:
        return self._guard(self._chaff_control, "pause")

    @Slot(result=str)
    def chaffResume(self) -> str:
        return self._guard(self._chaff_control, "resume")

    def _chaff_control(self, action: str) -> str:
        if not self._chaff_busy() or self._chaff_worker is None:
            return _dumps({"ok": False, "error": "not running"})
        getattr(self._chaff_worker, action)()
        self.lineReady.emit("chaff.log", f"[CTRL] {action} requested")
        return _dumps({"ok": True, "error": None})

    # ------------------------------------------------------------- devices

    def _device_list(self) -> list[dict]:
        """Inventory + safety marks + sanitize caps, as the DRIVES view
        renders them. Never raises: a failed probe degrades to unknown."""
        devices = inventory.enrich_with_nvme(inventory.list_devices(self._runner), self._runner)
        try:
            os_knames = safety.os_disk_knames(self._runner)
        except Exception:
            os_knames = set()
        out = []
        for dev in devices:
            caps = None
            if dev.is_nvme:
                raw = result_or_none(self._runner.run(["nvme", "id-ctrl", dev.path], timeout=30))
                parsed = parse_sanicap_from_id_ctrl(raw or "")
                caps = {
                    "supported": parsed.supported,
                    "block_erase": parsed.block_erase,
                    "crypto_erase": parsed.crypto_erase,
                    "overwrite": parsed.overwrite,
                }
            out.append(
                {
                    "kname": dev.kname,
                    "path": dev.path,
                    "type": dev.type,
                    "transport": dev.transport,
                    "size_bytes": dev.size_bytes,
                    "model": dev.model,
                    "serial": dev.serial,
                    "is_nvme": dev.is_nvme,
                    "os_disk": dev.kname in os_knames,
                    "mounted": dev.mounted,
                    "swap": dev.swap,
                    "mountpoints": dev.mountpoints(),
                    "partitions": [
                        {
                            "name": p.name,
                            "size_bytes": p.size_bytes,
                            "mountpoints": p.mountpoints,
                            "fstype": p.fstype,
                        }
                        for p in dev.partitions
                    ],
                    "sanitize": caps,
                }
            )
        return out

    @Slot(result=str)
    def deviceList(self) -> str:
        return self._guard(self._device_list_json, fallback="[]")

    def _device_list_json(self) -> str:
        return _dumps(self._device_list())

    @Slot(str, result=str)
    def deviceDetail(self, path: str) -> str:
        return self._guard(self._device_detail, path)

    def _device_detail(self, path: str) -> str:
        for dev in self._device_list():
            if dev["path"] == path or dev["kname"] == path.removeprefix("/dev/"):
                return _dumps(dev)
        return _dumps({"error": f"no device {path}"})

    # ---------------------------------------------------------------- wipe

    @Slot(str, result=str)
    def wipePlan(self, config_json: str) -> str:
        return self._guard(self._wipe_plan, config_json)

    def _wipe_plan(self, config_json: str) -> str:
        cfg = json.loads(config_json or "{}")
        devices = self._device_list()
        dev = next((d for d in devices if d["path"] == cfg.get("device")), None)
        if dev is None:
            return _dumps({"error": f"no device {cfg.get('device')}"})

        caps = dev["sanitize"] or {
            "supported": False,
            "block_erase": False,
            "crypto_erase": False,
            "overwrite": False,
        }
        from .wipe.inventory import DeviceInfo, Partition
        from .wipe.nvme import parse_sanicap

        info = DeviceInfo(
            kname=dev["kname"],
            path=dev["path"],
            type=dev["type"],
            size_bytes=dev["size_bytes"],
            model=dev["model"],
            serial=dev["serial"],
            transport=dev["transport"],
            partitions=[
                Partition(p["name"], p["size_bytes"], p["mountpoints"])
                for p in dev.get("partitions", [])
            ],
            swap=dev["swap"],
        )
        parsed_caps = parse_sanicap(
            (caps["block_erase"] << 0) | (caps["crypto_erase"] << 1) | (caps["overwrite"] << 2)
        )
        built = wipe_plan_mod.build_plan(
            info, parsed_caps, verify=bool(cfg.get("verify")), quick=bool(cfg.get("quick"))
        )

        verdict = safety.check_device(
            info,
            self._runner,
            allow_non_nvme=bool(self._app_settings.get("allow_non_nvme")),
            unmount_planned=bool(cfg.get("unmount")),
        )
        out = built.as_dict()
        out["blockers"] = verdict.hard_blockers + verdict.soft_blockers
        out["confirmation"] = {"word": "DESTROY", "device": dev["path"]}
        return _dumps(out)

    @Slot(str, result=str)
    def wipeStart(self, config_json: str) -> str:
        return self._guard(self._wipe_start, config_json)

    def _wipe_start(self, config_json: str) -> str:
        if self._busy():
            return _dumps({"ok": False, "error": "busy"})
        cfg = json.loads(config_json or "{}")
        plan = json.loads(self._wipe_plan(json.dumps(cfg)))
        if plan.get("error"):
            return _dumps({"ok": False, "error": plan["error"]})
        if plan.get("blockers"):
            return _dumps({"ok": False, "error": "; ".join(plan["blockers"])})
        if cfg.get("confirm_word") != "DESTROY" or cfg.get("confirm_device") != cfg.get("device"):
            return _dumps({"ok": False, "error": "confirmation mismatch"})

        worker_cfg = {
            "device": cfg.get("device"),
            "passes": plan.get("passes", []),
            "confirmation_word": cfg.get("confirm_word"),
            "confirmation_device": cfg.get("confirm_device"),
            "allow_non_nvme": bool(self._app_settings.get("allow_non_nvme")),
            "unmount_planned": bool(cfg.get("unmount")),
            "verify": bool(cfg.get("verify")),
            "quick": bool(cfg.get("quick")),
            "size_bytes": plan.get("size_bytes"),
            "dry_run": bool(cfg.get("dry_run")),
        }
        self._wipe_proc = WipeWorkerProc(worker_cfg, self)
        self._wipe_proc.workerEvent.connect(self._on_wipe_event)
        self._wipe_proc.finished.connect(self._on_wipe_finished)
        self._open_wipe_log(cfg.get("device") or "unknown")
        self._wipe_proc.start()
        self._emit_state()
        return _dumps({"ok": True, "error": None})

    def _open_wipe_log(self, device: str) -> None:
        """The GUI keeps the durable copy of the worker's stream: the root
        worker's stdout is a pipe, and Dispatch needs files on disk."""
        base = Path.home() / ".local" / "state" / "chafftafarian"
        base.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe = device.replace("/", "_").strip("_") or "device"
        self._wipe_log = base / f"wipe-{stamp}-{safe}.log"
        self._wipe_log.write_text(f"# wipe log {datetime.now(UTC).isoformat()} device={device}\n")

    def _on_wipe_event(self, event_json: str) -> None:
        self.lineReady.emit("wipe", event_json)
        try:
            ev = json.loads(event_json)
        except ValueError:
            return
        kind = ev.get("event")
        human = None
        if kind == "log":
            human = str(ev.get("text", ""))
        elif kind == "warning":
            human = f"[WARN] {ev.get('text', '')}"
        elif kind == "error":
            human = f"[ERROR] {ev.get('text', '')}"
        elif kind == "pass_start":
            human = f"PASS {ev.get('passno')}: {ev.get('detail', '')}"
        elif kind == "sanitize_progress":
            human = f"sanitize {ev.get('percent', 0):.0f}% ({ev.get('state', '')})"
        elif kind == "pass_progress":
            human = f"pass {ev.get('passno')} {ev.get('percent', 0):.1f}%"
        elif kind == "hello":
            human = f"worker pid {ev.get('pid')} uid {ev.get('uid')}"
        elif kind == "done":
            human = (
                f"done ok={ev.get('ok')} cancelled={ev.get('cancelled')} "
                f"passes {len(ev.get('passes_done') or [])}/"
                f"{ev.get('passes_total')}"
            )
        if human is not None:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.lineReady.emit("wipe.log", human)
            if self._wipe_log is not None:
                try:
                    with self._wipe_log.open("a", encoding="utf-8") as fh:
                        fh.write(f"[{stamp}] {human}\n")
                except OSError:
                    pass  # the live stream matters more than the archive

    def _on_wipe_finished(self, rc: int) -> None:
        proc, self._wipe_proc = self._wipe_proc, None
        if proc is not None:
            try:
                proc.workerEvent.disconnect(self._on_wipe_event)
                proc.finished.disconnect(self._on_wipe_finished)
            except Exception:
                pass
        self.lineReady.emit("wipe.log", f"[DONE] worker exited {rc}")
        self._emit_state()
        self.reportReady.emit(
            "wipe.summary",
            _dumps(
                {
                    "ok": rc == 0,
                    "cancelled": False,
                    "exit_code": rc,
                    "note": "per-pass detail in the event stream; the summary is "
                    "derived from the worker exit",
                }
            ),
        )

    @Slot(result=str)
    def wipeAbort(self) -> str:
        return self._guard(self._wipe_abort)

    def _wipe_abort(self) -> str:
        if self._wipe_proc is None or not self._wipe_proc.is_busy():
            return _dumps({"ok": False, "error": "not running"})
        if not self._wipe_proc.request_cancel():
            return _dumps({"ok": False, "error": "cancel already sent"})
        self.lineReady.emit("wipe.log", "[CTRL] cancel requested: stopping at the next checkpoint")
        return _dumps({"ok": True, "error": None})

    # ------------------------------------------------ runs / verify / cleanup

    def _runs_root(self) -> Path | None:
        target = self._app_settings.get("default_target")
        return Path(target).expanduser() if target else None

    @Slot(str, result=str)
    def runsList(self, path: str = "") -> str:
        return self._guard(self._runs_list, path, fallback="[]")

    def _runs_list(self, path: str) -> str:
        root = Path(path).expanduser() if path else self._runs_root()
        if root is None or not root.is_dir():
            return "[]"
        return _dumps(
            [
                {
                    "root": str(r.root),
                    "run_id": r.run_id,
                    "created_at": r.created_at,
                    "app_version": r.app_version,
                    "has_manifest": r.has_manifest,
                    "file_count": r.file_count,
                    "bytes_written": r.bytes_written,
                    "status": r.status,
                }
                for r in discover_runs(root)
            ]
        )

    @Slot(str, result=str)
    def runInspect(self, path: str) -> str:
        return self._guard(self._run_inspect, path)

    def _run_inspect(self, path: str) -> str:
        root = Path(path).expanduser()
        manifest_path = root / ".chaff-manifest.json"
        info: dict = {"root": str(root)}
        if manifest_path.is_file():
            try:
                m = read_manifest(manifest_path)
                info.update(
                    {
                        "run_id": m.run_id,
                        "created_at": m.created_at,
                        "status": m.status,
                        "bytes_written": m.bytes_written,
                        "profile": m.profile,
                        "seed": m.seed,
                        "file_count": len(m.files),
                        "largest": [
                            {
                                "path": f.relative_path,
                                "size_bytes": f.size,
                            }
                            for f in sorted(m.files, key=lambda f: f.size, reverse=True)[:5]
                        ],
                    }
                )
            except Exception as exc:
                info["error"] = f"manifest unreadable: {exc}"
        else:
            info["error"] = "no manifest (cancelled or pre-manifest run)"
        return _dumps(info)

    @Slot(str, str, result=str)
    def verifyStart(self, path: str, mode: str = "full") -> str:
        return self._guard(self._verify_start, path, mode)

    def _verify_start(self, path: str, mode: str) -> str:
        if self._verify_busy:
            return _dumps({"ok": False, "error": "busy"})
        try:
            vmode = VerificationMode(mode)
        except ValueError:
            return _dumps({"ok": False, "error": f"unknown mode {mode!r}"})
        self._verify_busy = True
        threading.Thread(
            target=self._verify_worker,
            args=(Path(path).expanduser(), vmode),
            daemon=True,
            name="verify",
        ).start()
        return _dumps({"ok": True, "error": None})

    def _verify_worker(self, run_root: Path, mode: VerificationMode) -> None:
        try:
            report = verify_run(run_root, mode)
            data = report.to_dict()
            # the per-file results can be enormous; the bridge carries the
            # affected files and the counts, not every INTACT row
            data["affected"] = [r.to_dict() for r in report.affected[:50]]
            del data["results"]
            data["ok"] = report.ok
        except Exception as exc:
            traceback.print_exc()
            data = {"run_root": str(run_root), "ok": False, "error": str(exc)}
        finally:
            self._verify_busy = False
        self.reportReady.emit("verify", _dumps(data))

    @Slot(str, str, result=str)
    def runCleanup(self, path: str, action: str) -> str:
        return self._guard(self._run_cleanup, path, action)

    def _run_cleanup(self, path: str, action: str) -> str:
        try:
            mode = CompletionAction(action)
        except ValueError:
            return _dumps({"ok": False, "error": f"unknown action {action!r}"})
        if mode is CompletionAction.KEEP:
            return _dumps({"ok": False, "error": "nothing to do for 'keep'"})
        result = CleanupManager().clean(Path(path).expanduser(), mode)
        return _dumps(
            {
                "ok": True,
                "run_root": str(result.run_root),
                "trashed": result.trashed,
                "warnings": list(result.warnings),
            }
        )

    # ------------------------------------------------------------- settings

    @Slot(str, result=str)
    def settingsGet(self) -> str:
        return self._guard(self._settings_get)

    def _settings_get(self) -> str:
        def spec_json(specs: dict) -> list[dict]:
            return [
                {
                    "name": s.name,
                    "default": s.default,
                    "kind": s.kind,
                    "choices": list(s.choices),
                    "help": s.help,
                    "label": s.label,
                    "group": s.group,
                }
                for s in specs.values()
            ]

        values = {}
        for s in config.DEVICE_SPECS.values():
            raw = self._app_settings.get(s.name, s.default if s.kind != "bool" else False)
            values[s.name] = ("true" if raw else "false") if s.kind == "bool" else str(raw)
        return _dumps(
            {
                "specs": {"device": spec_json(config.DEVICE_SPECS)},
                "values": {"device": values},
            }
        )

    @Slot(str, result=str)
    def settingsSave(self, values_json: str) -> str:
        return self._guard(self._settings_save, values_json)

    def _settings_save(self, values_json: str) -> str:
        values = json.loads(values_json) if values_json and values_json.strip() else {}
        if not isinstance(values, dict):
            raise ValueError("values_json must be {device: {...}}")
        errors: list[str] = []
        incoming = values.get("device") or {}
        for name, spec in config.DEVICE_SPECS.items():
            if name not in incoming:
                continue
            raw = str(incoming[name]).strip().lower()
            if spec.kind == "bool":
                if raw not in ("true", "false"):
                    errors.append(f"{name} must be true or false")
                    continue
                self._app_settings[name] = raw == "true"
            else:
                self._app_settings[name] = str(incoming[name])
        if not errors:
            config.save_app_settings(self._app_settings)
        return _dumps({"ok": not errors, "errors": errors})

    # ------------------------------------------------------------------ logs

    @Slot(result=str)
    def logsList(self) -> str:
        return self._guard(self._logs_list, fallback="[]")

    def _logs_list(self) -> str:
        out = []

        def _iso(ts: float) -> str:
            return datetime.fromtimestamp(ts, tz=UTC).isoformat(timespec="seconds")

        base = Path.home() / ".local" / "state" / "chafftafarian"
        if base.is_dir():
            for p in sorted(base.glob("wipe-*.log"), reverse=True):
                try:
                    st = p.stat()
                    out.append(
                        {
                            "engine": "wipe",
                            "name": p.name,
                            "path": str(p),
                            "size_bytes": st.st_size,
                            "mtime": _iso(st.st_mtime),
                        }
                    )
                except OSError:
                    continue
        root = self._runs_root()
        if root is not None and root.is_dir():
            for run in discover_runs(root):
                journal = run.root / ".chaff-journal.jsonl"
                if journal.is_file():
                    try:
                        st = journal.stat()
                        out.append(
                            {
                                "engine": "chaff",
                                "name": f"{run.run_id} journal",
                                "path": str(journal),
                                "size_bytes": st.st_size,
                                "mtime": _iso(st.st_mtime),
                            }
                        )
                    except OSError:
                        continue
        return _dumps(out)

    @Slot(str, str, result=str)
    def logRead(self, path: str, tail_bytes: str = "65536") -> str:
        return self._guard(
            self._log_read,
            path,
            tail_bytes,
            fallback='{"path": "", "text": "", "error": "internal error"}',
        )

    def _log_read(self, path: str, tail_bytes: str) -> str:
        p = Path(path)
        try:
            n = max(0, int((tail_bytes or "65536").strip()))
        except ValueError:
            n = 65536
        try:
            with p.open("rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - n))
                return _dumps({"path": str(p), "text": fh.read().decode("utf-8", errors="replace")})
        except FileNotFoundError:
            return _dumps({"path": path, "text": "", "error": "not found"})
        except OSError as exc:
            return _dumps({"path": path, "text": "", "error": str(exc)})

    @Slot(str)
    def tailStart(self, path: str) -> None:
        self._guard(self._tail_start, path)

    def _tail_start(self, path: str) -> None:
        old, self._tail = self._tail, None
        if old is not None:
            old.stop()  # daemon thread exits within one poll interval
        t = FileTail()

        def on_line(line: str) -> None:
            self.lineReady.emit("tail", line)

        try:
            t.start(path, on_line)
            self._tail = t
        except RuntimeError:
            t.stop()

    @Slot()
    def tailStop(self) -> None:
        self._guard(self._tail_stop)

    def _tail_stop(self) -> None:
        t, self._tail = self._tail, None
        if t is not None:
            t.stop()

    # ------------------------------------------------------------------ os

    @Slot(str, result=str)
    def pickDirectory(self, current: str) -> str:
        try:
            start = current or self._app_settings.get("default_target") or str(Path.home())
            parent = p if isinstance(p := self.parent(), QWidget) else None
            return QFileDialog.getExistingDirectory(parent, "Choose directory", start) or ""
        except Exception:
            traceback.print_exc()
            return ""

    @Slot(str)
    def openPath(self, path: str) -> None:
        self._guard(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).expanduser())))
        )

    @Slot(str)
    def revealFile(self, path: str) -> None:
        def _reveal() -> None:
            d = Path(path).expanduser().parent
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))

        self._guard(_reveal)

    @Slot(str)
    def viewReady(self, view: str) -> None:
        self.viewRendered.emit(view)
