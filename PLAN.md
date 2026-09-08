# Chafftafarian — Implementation Plan & Checklist

Execution order for building and verifying the app. Phases 0–8 are
complete as of v0.1.0; the checklists double as regression recipes.
docs/SPEC.md is the authoritative behavior spec.

## Phase 0 — Scaffold

- [x] Repo layout mirroring video-hls: `apps/desktop/`, `packaging/`,
      `docs/`, `.github/workflows/`
- [x] pyproject (py3.12, deps union, hatch packages both src trees)
- [x] LICENSE (MIT), .gitignore, .python-version, README stub, git init

## Phase 1 — Vendor the chaff core

- [x] Copy `chaff_generator` minus `gui/`; copy tests minus `ui/`; copy
      `scripts/` (benchmark.py is a test dependency); write VENDORED.md
- [x] Keep the `chaff_generator` import name unchanged
- [x] Gate: `uv sync --extra dev && uv run pytest` green (311 tests);
      `chaff generate --target /tmp/x --size 1MiB --seed 7` produces a run

## Phase 2 — GUI shell port

- [x] app.py / __main__.py (VIEWS, screenshot harness, `--wipe-worker`
      dispatch before Qt import), config.py, api.py skeleton
- [x] Frontend shell: main.js (6 exits), shell.js, api.js, content.js,
      mock.js, components + tokens verbatim, new icon
- [x] Gate: `--screenshot all` non-blank; JS parses; `?mock=1` backend

## Phase 3 — Chaff GUI

- [x] chaff_adapter.py (QThread; events → `chaff` / `chaff.log` /
      `chaff.summary`)
- [x] Slots: chaffPreflight/Start/Cancel/Pause/Resume, runsList,
      runInspect, verifyStart, runCleanup
- [x] CHAFF view (form → preflight → live run → summary) and RUNS view
      (history → inspect → verify → typed-DELETE cleanup)
- [x] Gate: `tests/test_api.py` (E2E generation through the bridge,
      verify INTACT, cleanup refusal, settings round-trip)

## Phase 4 — Device layer

- [x] tools.py seam (ToolRunner protocol; subprocess + fake runners;
      missing-command and timeout as results, not exceptions)
- [x] inventory.py (lsblk -Jb parsing, nested mountpoints, [SWAP], zram/
      rom exclusion, nvme list enrichment, dev_dir test hatch)
- [x] nvme.py (sanicap/SANACT/SSTAT/SPROG decode; both nvme-cli output
      shapes; command builders)
- [x] safety.py (OS-disk via findmnt+proc-swaps, swap, mounted, non-NVMe
      gate; hard vs soft; recheck_before_pass)
- [x] Gate: `tests/test_wipe.py` parser/inventory/safety sections

## Phase 5 — Wipe pipeline

- [x] plan.py (paranoid 6 / quick / verify), writer.py (pattern writes,
      O_DIRECT + fallback, ENOSPC-normal, sampled verify, cancel),
      orchestrator.py (safety re-checks, sanitize-log-only monitoring,
      sanact=0 abort + drain), protocol.py (JSONL), worker.py (root
      entry, re-validation), worker_spawn.py (pkexec argv, control-dir
      cancel, reader thread)
- [x] wipeStart/wipeAbort/wipePlan/deviceList/deviceDetail slots; wipe
      logs archived to ~/.local/state/chafftafarian/
- [x] DRIVES + SANITIZE views (typed DESTROY + kname arming, per-pass
      progress lanes)
- [x] Gate: fake end-to-end wipe on temp files; cancel mid-write and
      mid-sanitize; sanitize failure propagation; headless worker
      dispatch

## Phase 6 — Settings + Dispatch

- [x] FieldSpec settings view (allow_non_nvme, persisted app.json)
- [x] Dispatch: logsList/logRead/tailStart/tailStop over wipe archives
      and chaff journals

## Phase 7 — Docs

- [x] docs/SPEC.md (this file's authoritative sibling)
- [x] PLAN.md (this file), README.md, VENDORED.md, BRIDGE.md

## Phase 8 — Packaging + CI

- [x] packaging/appimage.sh (name, no engine tree, default-pack
      collection check, `--selftest-wipe-plan` smoke, drives screenshot)
- [x] CI split three ways: `./package.sh` builds + smoke-tests locally
      (wraps packaging/appimage.sh); `ci.yml` runs on workflow_dispatch
      only (never on pushes to main); `release.yml` on v* tags publishes
      the Release

## Regression recipe (run from apps/desktop)

```bash
uv sync --extra dev
uv run pytest                                  # all suites
uv run python -m chafftafarian --screenshot all --out /tmp/shots --size 1600x1000
python -m http.server -d frontend 8765         # then open ?mock=1
uv run chaff generate --target /tmp/chaff-regression --size 4MiB --seed 42
uv run chaff verify /tmp/chaff-regression/Chaff_Run_*
```

From the repo root:

```bash
packaging/appimage.sh                          # AppImage + smoke
bash -n packaging/appimage.sh
```

## Human-only real-device checklist

CI and dev machines must never see this path. On a machine with a scratch
NVMe drive you are willing to destroy:

1. `nvme id-ctrl /dev/nvmeXn1` — compare sanicap bits against the DRIVES
   view and the vendor datasheet (crypto/overwrite bit order, NVMe 1.4 vs
   2.0 caveat in SPEC §2).
2. Build the plan on the scratch drive — dry run only.
3. Start the wipe, cancel during pass 2 — confirm writes stop and the
   summary reports cancelled.
4. Start again, cancel during sanitize — confirm the abort path:
   `sudo nvme sanitize-log /dev/nvmeXn1` should show the state leaving
   in-progress after `--sanact=0`.
5. Full paranoid wipe with verification.
6. Spot check the result: `sudo dd if=/dev/nvmeXn1 bs=512 count=20 |
   hexdump -C` (all zeros) and compare with the verify pass verdict.
7. From the AppImage: confirm the pkexec dialog appears (the
   extract-and-run path) and the worker streams progress.
8. `mkfs.ext4` the wiped drive, mount it, fill it with chaff from EXIT 03,
   verify the run, clean it up.

## Deferred (documented, not built)

- Windows build (chaff native; device ops via `wsl --mount --bare` —
  Win11-only, fragile; the Control Room WSL2 backend pattern applies)
- Guided wipe → mkfs → mount → chaff flow (v1 documents the manual steps)
- SANACT=1 crypto-erase and SANACT=3 overwrite actions in the plan builder
- polkit .policy with a stable path (needs a system install, not AppImage)
- SATA/HDD-specific guidance pages from the wiper's TODO list
- Chaff resume, archives, corruption lab (upstream deferred features)
