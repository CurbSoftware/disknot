"""App settings and (later) the FieldSpec tables for both feature surfaces.

Chafftafarian's own settings live in a platformdirs config dir as app.json;
chaff run values are constructed per-run in the CHAFF view and are not
persisted here (the chaff core keeps its own run history). Device-side
defaults with safety consequences live here so the root worker can re-read
the same truth the GUI shows.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_dir

from . import __version__


def app_settings_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path(user_config_dir("chafftafarian"))
    return root / "app.json"


def load_app_settings() -> dict:
    """Load the app's own settings; missing or corrupt file yields defaults."""
    defaults = default_app_settings()
    try:
        data = json.loads(app_settings_path().read_text())
    except (OSError, ValueError):
        return defaults
    if not isinstance(data, dict):
        return defaults
    defaults.update(data)
    return defaults


def save_app_settings(settings: dict) -> None:
    """Persist app settings atomically."""
    path = app_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def default_app_settings() -> dict:
    return {
        "version": __version__,
        # Safety gates. allow_non_nvme enables wiping SATA/USB/loop devices;
        # it exists so loop-device testing is possible, and is off by default.
        "allow_non_nvme": False,
        # Default chaff target directory for the CHAFF view's picker.
        "default_target": "",
        # Window geometry persistence (set by the frontend via settingsSave).
        "window": {},
        # Misc view preferences.
        "prefs": {"confirm_shown": False},
    }


@dataclass(frozen=True)
class FieldSpec:
    """One user-facing setting: name, default, and UI metadata.

    Mirrors Control Room's config.FieldSpec so the SETTINGS view renders
    both engines' surfaces from one component.
    """

    name: str
    default: str
    kind: str = "str"  # str | bool | int | choice | path
    choices: tuple[str, ...] = ()
    help: str = ""
    label: str = ""
    group: str = ""


# Device-side settings shown in SETTINGS. The chaff surface is per-run
# (constructed in the CHAFF view), so it does not persist here.
DEVICE_SPECS: dict[str, FieldSpec] = {
    s.name: s
    for s in (
        FieldSpec(
            name="allow_non_nvme",
            default="false",
            kind="bool",
            label="Allow non-NVMe devices",
            group="Devices",
            help="Enables sanitize/wipe on SATA, USB and loop devices. "
            "NVMe-only is the safe default; loop devices are how the "
            "wipe path is tested without spare NVMe hardware.",
        ),
    )
}
