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

## Commands

```bash
./package.sh                                    # build + smoke the AppImage
cd apps/desktop
uv sync --extra dev
uv run pytest                                   # vendored + app suites
uv run python -m chafftafarian                  # the app
uv run python -m chafftafarian --screenshot all --out /tmp/shots
uv run ruff check src/chafftafarian && uv run mypy src/chafftafarian
```

CI runs on demand only (ci.yml, workflow_dispatch); releases on v* tags
(release.yml); pushes to main trigger nothing.
