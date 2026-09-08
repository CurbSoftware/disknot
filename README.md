# Chafftafarian

Chaff generation, NVMe tools, and drive sanitization in one desktop app.

Chafftafarian fills wiped or fresh storage with realistic synthetic files
(documents, mail, spreadsheets, payload blobs) that are deterministic,
verifiable, and confined to their own run directories — and it sanitizes
NVMe drives properly: the controller Sanitize command bracketing overwrite
passes, with per-pass progress, cancellation that works, and sampled
verification.

The lineage is the point: wipe the drive, then fill it with chaff.

## Install

Download `chafftafarian-x86_64.AppImage` from Releases, make it
executable, run it. Linux only in v1.

Device operations need root; the app asks through pkexec (a system
password dialog) at the moment an operation starts — the GUI itself never
runs as root. Requirements: `nvme-cli` for NVMe probing and sanitize
(`sudo apt install nvme-cli` or the equivalent).

## The six exits

| Exit | View | What it does |
|---|---|---|
| 01 | DRIVES | the drive bay: geometry, mounts, OS-disk flags, sanitize capabilities |
| 02 | SANITIZE | plan → typed DESTROY + device name → per-pass wipe with live progress and cancel |
| 03 | CHAFF | configure a run, preflight it, generate, watch it fill |
| 04 | RUNS | every run on record: inspect, verify (full/sampled), cleanup |
| 05 | SETTINGS | the few persisted knobs (including the non-NVMe device gate) |
| 06 | DISPATCH | log archives and live tails |

Keyboard: `1`–`6` jump exits, `Esc` backs out of dialogs.

## Safety model (the short version)

- The disk your OS runs on is never wipeable. No override exists.
- Active swap is never wipeable.
- Mounted drives are refused until unmounted — by you, or explicitly by
  the worker (logged, and re-checked before every pass).
- Non-NVMe devices are off by default (a settings switch exists for loop-
  device testing).
- Arming a wipe requires typing DESTROY **and** the device's kernel name.
- The root worker re-validates everything itself; it never trusts the GUI.
- Chaff never touches devices and never needs privileges. Filling free
  space is not sanitization and the app says so.

## Develop

```bash
cd apps/desktop
uv sync --extra dev
uv run python -m chafftafarian                  # the app
uv run python -m chafftafarian --screenshot all --out ../../screenshots
uv run pytest                                   # vendored + app tests
python -m http.server -d frontend 8765          # browser mode, then ?mock=1
uv run chaff generate --target /tmp/x --size 1MiB   # vendored CLI
```

Packaging and releases:

- **Local**: `./package.sh` — builds the AppImage end to end and
  smoke-tests it (same script CI uses).
- **CI**: `ci.yml` runs on demand only (Actions → ci → Run); pushes to
  main do not trigger anything.
- **Releases**: `release.yml` fires on `v*` tags — `git tag v0.1.0 && git
  push origin v0.1.0` publishes a Release with the AppImage.

Documentation: docs/SPEC.md (behavior spec, decode tables, safety model),
PLAN.md (phases and the human-only real-device checklist),
apps/desktop/BRIDGE.md (the Qt ↔ HTML bridge contract), VENDORED.md
(provenance of the chaff core and the Control Room patterns).

## License

MIT. The chaff core is vendored from the Chafftafarian-Chaff-Generator
repository (MIT, CurbSoftware Inc); GUI architecture and packaging are
adapted from video-hls Control Room (MIT).
