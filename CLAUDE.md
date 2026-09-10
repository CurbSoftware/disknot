# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working
with code in this repository.

## Writing style

No em-dashes and no emojis in code, comments, or documentation. Write
user-facing copy in a direct, human voice: short sentences, no filler, no
promo words. Numeric ranges may use en-dashes (1-6, Phases 0-8).

The vendored `chaff_generator` tree is the one exception: it stays
byte-identical to upstream (see VENDORED.md), so its punctuation is
upstream's business.

## Start here

PLAN.md has the phase history and the verification recipes.
docs/SPEC.md is the behavior spec (decode tables, safety model, the
boundary between the chaff core and the device side). apps/desktop/
BRIDGE.md is the frozen Qt-HTML contract: additive fields only.

## Architecture

Two source trees under `apps/desktop/src/`:

- `chafftafarian/` is this app. `app.py` builds the Qt window and embeds
  `frontend/index.html` in a QWebEngineView; `api.py` is the one
  QWebChannel object (id "api") the frontend talks to. Slots run on the
  GUI thread; worker threads only emit signals.
- `chaff_generator/` is vendored upstream, kept byte-identical
  (VENDORED.md). Do not edit it or its test suites here; fixes belong
  upstream and come back as a re-vendor.

Chaff runs in-process on a QThread (`chaff_adapter.py`). Device
operations never run in the GUI process: `worker_spawn.py` launches the
root worker via pkexec (`__main__.py --wipe-worker`, dispatched before
any Qt import), reads its JSONL event stream (`wipe/protocol.py`), and
cancels via a control file because the unprivileged GUI cannot signal a
root process.

The wipe layer, bottom up: `wipe/tools.py` (ToolRunner seam; subprocess
plus fake runners for tests) → `inventory.py` (lsblk/nvme parsing) →
`safety.py` (hard and soft refusals: OS disk, swap, mounts, non-NVMe
gate) → `plan.py` / `writer.py` / `orchestrator.py` → `worker.py`,
which re-validates everything and never trusts the GUI.

The frontend is dependency-free JS in `frontend/js/`. Tests:
`apps/desktop/tests/` root holds the app tests (`test_api.py` is E2E
through the bridge, `test_wipe.py` uses fake ToolRunners); the
`unit/integration/renderers/safety` subdirectories are vendored
upstream suites.

## Commands

```bash
./package.sh                                    # build + smoke the AppImage
cd apps/desktop
uv sync --extra dev
uv run pytest                                   # vendored + app suites
uv run pytest tests/test_wipe.py                # one file
uv run pytest tests/unit -k "bank and not pack" # by keyword
uv run python -m chafftafarian                  # the app
uv run python -m chafftafarian --screenshot all --out /tmp/shots
uv run chaff generate --target /tmp/x --size 1MiB   # vendored CLI
python -m http.server -d frontend 8765          # browser mode, then ?mock=1
uv run ruff check src/chafftafarian && uv run mypy src/chafftafarian
```

Lint and types cover `src/chafftafarian` only; the vendored tree is
upstream's.

CI runs on demand only (ci.yml, workflow_dispatch); releases on v* tags
(release.yml); pushes to main trigger nothing.
