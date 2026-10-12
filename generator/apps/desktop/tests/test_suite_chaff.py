"""Chaff preflight, including read-only targets. See docs/tdd-plan.md."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tests.conftest import make_config

from chaff_generator.core.engine import ChaffEngine
from chaff_generator.core.errors import ChaffError
from chaff_generator.core.filesystem import check_writable_dir


def _pyside_unavailable() -> str | None:
    try:
        import PySide6.QtCore  # noqa: F401
    except Exception as exc:
        return f"PySide6 cannot be imported: {exc!r}"
    return None


def _config(target: Path) -> str:
    return json.dumps(
        {
            "target": {
                "path": str(target),
                "mode": "exact",
                "amount": "1 MiB",
                "reserve": "64 MiB",
            },
            "profile": "storage-test",
            "seed": 7,
            "completion": "keep",
        }
    )


@pytest.fixture()
def api(tmp_path, monkeypatch):
    reason = _pyside_unavailable()
    if reason:
        pytest.skip(reason)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.instance() or QCoreApplication([])
    from chafftafarian.api import Api

    return Api()


def test_preflight_on_a_writable_directory(tmp_path):
    summary = ChaffEngine(make_config(tmp_path)).preflight()
    assert summary.target_path == tmp_path.resolve()
    assert summary.free_bytes > 0
    assert list(tmp_path.glob(".chaff-write-probe-*")) == []


def test_preflight_refuses_a_missing_directory(tmp_path):
    missing = tmp_path / "no-such-dir"
    with pytest.raises(ChaffError, match="does not exist"):
        check_writable_dir(missing)
    with pytest.raises(ChaffError, match="does not exist"):
        ChaffEngine(make_config(missing)).preflight()


def test_preflight_refuses_a_file(tmp_path):
    target = tmp_path / "not-a-dir"
    target.write_text("x")
    with pytest.raises(ChaffError, match="not a directory"):
        check_writable_dir(target)


def test_preflight_refuses_a_read_only_directory(tmp_path):
    target = tmp_path / "ro"
    target.mkdir()
    target.chmod(0o555)
    try:
        with pytest.raises(ChaffError, match="Permission denied"):
            check_writable_dir(target)
        with pytest.raises(ChaffError, match="Permission denied"):
            ChaffEngine(make_config(target)).preflight()
        assert list(target.glob(".chaff-write-probe-*")) == []
    finally:
        target.chmod(0o755)


def test_preflight_reports_a_read_only_filesystem(tmp_path, monkeypatch):
    def boom(self, *args, **kwargs):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(Path, "write_text", boom)
    with pytest.raises(ChaffError, match="Cannot write"):
        check_writable_dir(tmp_path)


def test_api_preflight_ok(api, tmp_path):
    out = json.loads(api.chaffPreflight(_config(tmp_path)))
    assert out["ok"] is True
    assert out["free_bytes"] > 0
    assert out["target_path"] == str(tmp_path)
    assert list(tmp_path.glob(".chaff-write-probe-*")) == []


def test_api_preflight_read_only_is_not_ok(api, tmp_path):
    target = tmp_path / "ro"
    target.mkdir()
    target.chmod(0o555)
    try:
        out = json.loads(api.chaffPreflight(_config(target)))
        assert out == {"ok": False, "error": "internal error"}
        assert list(target.iterdir()) == []
    finally:
        target.chmod(0o755)


def test_api_preflight_missing_directory_is_not_ok(api, tmp_path):
    out = json.loads(api.chaffPreflight(_config(tmp_path / "missing")))
    assert out == {"ok": False, "error": "internal error"}


def test_api_preflight_file_target_is_not_ok(api, tmp_path):
    target = tmp_path / "file"
    target.write_text("x")
    out = json.loads(api.chaffPreflight(_config(target)))
    assert out == {"ok": False, "error": "internal error"}
    assert target.read_text() == "x"
