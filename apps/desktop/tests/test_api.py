"""Api bridge slots, exercised headlessly: chaff preflight/start/summary,
runs listing, verify, and the safety of runCleanup."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _pyside_unavailable_reason() -> str | None:
    try:
        import PySide6.QtCore  # noqa: F401
    except Exception as exc:  # ImportError, or a broken Qt load
        return f"PySide6 cannot be imported: {exc!r}"
    return None


pytestmark = pytest.mark.skipif(
    _pyside_unavailable_reason() is not None, reason=_pyside_unavailable_reason() or "unreachable"
)


@pytest.fixture(scope="session")
def qapp():
    """Cross-thread signals (worker -> Api) are queued: the test loop must
    pump events for them to deliver."""
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])
    return app


@pytest.fixture()
def api(tmp_path, monkeypatch, qapp):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    from chafftafarian.api import Api

    return Api()


def _config(target: Path, amount: str = "1 MiB", seed: int = 7) -> str:
    return json.dumps(
        {
            "target": {"path": str(target), "mode": "exact", "amount": amount, "reserve": "64 MiB"},
            "profile": "storage-test",
            "seed": seed,
            "completion": "keep",
        }
    )


def _wait_summary(api, qapp, timeout_s: float = 60.0) -> dict:
    seen: list[tuple[str, dict]] = []
    api.reportReady.connect(lambda ch, js: seen.append((ch, json.loads(js))))
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        for ch, data in seen:
            if ch == "chaff.summary":
                return data
        time.sleep(0.05)
    pytest.fail("no chaff.summary report arrived")


def test_bootstrap_shape(api):
    boot = json.loads(api.getBootstrap())
    for key in ("core", "tools", "busy", "kind", "app_settings"):
        assert key in boot, f"missing {key}"
    assert boot["core"]["ready"] is True
    assert "pkexec" in boot["tools"]


def test_preflight_reports_free_space(api, tmp_path):
    out = json.loads(api.chaffPreflight(_config(tmp_path)))
    assert out["ok"] is True
    assert out["free_bytes"] > 0
    assert out["target_path"] == str(tmp_path)


def test_generation_end_to_end(api, qapp, tmp_path):
    ack = json.loads(api.chaffStart(_config(tmp_path)))
    assert ack["ok"], ack
    summary = _wait_summary(api, qapp)
    assert summary["ok"] is True
    assert summary["status"] == "completed"
    assert summary["files_created"] >= 1
    assert Path(summary["run_root"]).is_dir()

    # the run is discoverable and inspectable
    runs = json.loads(api.runsList(str(tmp_path)))
    assert any(r["run_id"] == summary["run_id"] for r in runs)
    detail = json.loads(api.runInspect(summary["run_root"]))
    assert detail["seed"] == 7
    assert detail["file_count"] >= 1


def test_verify_reports_intact(api, qapp, tmp_path):
    ack = json.loads(api.chaffStart(_config(tmp_path)))
    assert ack["ok"]
    summary = _wait_summary(api, qapp)
    assert summary["ok"]

    seen: list[dict] = []
    api.reportReady.connect(lambda ch, js: seen.append(json.loads(js)) if ch == "verify" else None)
    assert json.loads(api.verifyStart(summary["run_root"], "full"))["ok"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not seen:
        qapp.processEvents()
        time.sleep(0.05)
    report = seen[-1]
    assert report["ok"] is True
    assert report["counts"]["INTACT"] == summary["files_created"]


def test_cleanup_refuses_non_run_directories(api, tmp_path):
    innocent = tmp_path / "not-a-run"
    innocent.mkdir()
    (innocent / "precious.txt").write_text("keep me")
    out = json.loads(api.runCleanup(str(innocent), "delete"))
    assert out["ok"] is False
    assert (innocent / "precious.txt").exists()


def test_cleanup_removes_completed_run(api, qapp, tmp_path):
    assert json.loads(api.chaffStart(_config(tmp_path)))["ok"]
    summary = _wait_summary(api, qapp)
    assert summary["ok"]
    out = json.loads(api.runCleanup(summary["run_root"], "delete"))
    assert out["ok"], out
    assert not Path(summary["run_root"]).exists()


def test_control_slots_when_idle(api):
    assert json.loads(api.chaffCancel())["ok"] is False
    assert json.loads(api.chaffPause())["ok"] is False


def test_settings_round_trip(api):
    got = json.loads(api.settingsGet())
    assert got["values"]["device"]["allow_non_nvme"] == "false"
    out = json.loads(api.settingsSave(json.dumps({"device": {"allow_non_nvme": "true"}})))
    assert out["ok"], out
    got = json.loads(api.settingsGet())
    assert got["values"]["device"]["allow_non_nvme"] == "true"
