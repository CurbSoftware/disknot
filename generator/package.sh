#!/usr/bin/env bash
# Package Disknot locally: build the AppImage end to end and
# smoke-test it (selftest + list-views + offscreen drives screenshot).
# Produces packaging/chafftafarian-x86_64.AppImage. Requires
# apps/desktop/.venv (cd apps/desktop && uv sync --extra dev).
set -Eeuo pipefail
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/packaging/appimage.sh" "$@"
