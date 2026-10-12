"""The published AppImage name is disknot-<version>-x86_64.AppImage. See docs/tdd-plan.md."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chafftafarian import __version__

GENERATOR = Path(__file__).resolve().parents[3]
REPO = GENERATOR.parent
EXPECTED = "disknot-0.1.0-x86_64.AppImage"
PUBLISHED = Path("/home/user/projects/published/disknot")


def _version_from_pyproject() -> str:
    text = (GENERATOR / "apps/desktop/pyproject.toml").read_text()
    match = re.search(r'^version = "([^"]+)"', text, re.M)
    assert match
    return match.group(1)


def test_version_is_0_1_0():
    assert _version_from_pyproject() == "0.1.0"
    assert __version__ == "0.1.0"
    assert f"disknot-{__version__}-x86_64.AppImage" == EXPECTED


def test_appimage_script_uses_the_versioned_name():
    text = (GENERATOR / "packaging/appimage.sh").read_text()
    assert 'APPIMAGE_FILE="disknot-${APP_VERSION}-x86_64.AppImage"' in text
    assert "LDAI_OUTPUT" in text
    assert "Disknot-x86_64.AppImage" in text


def test_package_script_points_at_the_versioned_name():
    text = (GENERATOR / "package.sh").read_text()
    assert "disknot-<version>-x86_64.AppImage" in text
    assert "appimage.sh" in text


def test_publish_script_copies_the_versioned_name():
    text = (REPO / "scripts/publish.sh").read_text()
    assert 'appimage="generator/packaging/disknot-${version}-x86_64.AppImage"' in text
    assert '"$DEST/disknot-${version}-x86_64.AppImage"' in text
    assert "chafftafarian-x86_64.AppImage" in text


def test_release_workflow_globs_the_versioned_name():
    text = (GENERATOR / ".github/workflows/release.yml").read_text()
    ci = (GENERATOR / ".github/workflows/ci.yml").read_text()
    assert "packaging/disknot-*-x86_64.AppImage" in text
    assert "packaging/disknot-*-x86_64.AppImage" in ci


def test_published_folder_has_the_versioned_appimage():
    if not PUBLISHED.is_dir():
        pytest.skip("published disknot folder is not on this machine")
    names = {
        path.name
        for path in PUBLISHED.iterdir()
        if path.is_file() and path.suffix == ".AppImage"
    }
    assert names == {EXPECTED}


def test_unversioned_name_is_not_what_packaging_writes():
    text = (GENERATOR / "packaging/appimage.sh").read_text()
    assert 'APPIMAGE_FILE="disknot-x86_64.AppImage"' not in text
    assert 'APPIMAGE_FILE="chafftafarian-x86_64.AppImage"' not in text
