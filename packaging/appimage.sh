#!/usr/bin/env bash
# Build the Chafftafarian AppImage.
#
#   packaging/appimage.sh
#
# Adapted from video-hls Control Room's appimage.sh. Differences: there is
# no external engine tree to bundle — the chaff core rides --paths src and
# its data pack is collected explicitly; the smoke test adds
# --selftest-wipe-plan (wipe package + decode, no devices or root).
#
# Requirements: apps/desktop/.venv (uv sync --extra dev), network on first
# run for linuxdeploy. No FUSE needed (APPIMAGE_EXTRACT_AND_RUN is used
# everywhere).

set -Eeuo pipefail

PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$PKG_DIR/.." && pwd)"
DESKTOP_APP="$REPO/apps/desktop"
VENV_PY="$DESKTOP_APP/.venv/bin/python"
APP_NAME="chafftafarian"
DIST="$DESKTOP_APP/dist/$APP_NAME"
APPDIR="$PKG_DIR/$APP_NAME.AppDir"
CACHE="$PKG_DIR/cache"
SMOKE="$PKG_DIR/smoke"
SHARE="share/$APP_NAME"

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'FATAL: %s\n' "$*" >&2; exit 1; }

[[ -x "$VENV_PY" ]] || die "no venv python at $VENV_PY — run: cd apps/desktop && uv sync --extra dev"
[[ -d "$DESKTOP_APP/src/chaff_generator" ]] || die "vendored chaff core missing"

# --- icon: SVG -> PNG via the venv's QtSvg -----------------------------------

render_icon() {
  local svg="$DESKTOP_APP/frontend/assets/icon.svg" out="$PKG_DIR/icon-512.png"
  [[ -f "$svg" ]] || die "icon source missing: $svg"
  if [[ -s "$out" && ! "$svg" -nt "$out" ]]; then return 0; fi
  log "rendering $out from icon.svg"
  QT_QPA_PLATFORM=offscreen "$VENV_PY" - "$svg" "$out" <<'PY'
import sys
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

svg, out = sys.argv[1], sys.argv[2]
renderer = QSvgRenderer(svg)
image = QImage(512, 512, QImage.Format_ARGB32)
image.fill(Qt.transparent)
painter = QPainter(image)
renderer.render(painter)
painter.end()
if not image.save(out, "PNG"):
    sys.exit("could not write " + out)
PY
}

# --- PyInstaller onedir build -------------------------------------------------

build_bundle() {
  log "pyinstaller build (this takes a few minutes)"
  (
    cd "$DESKTOP_APP"
    # --collect-data: the chaff core resolves its default pack through
    # importlib.resources; PyInstaller does not see that dependency.
    .venv/bin/pyinstaller --noconfirm --windowed --onedir \
      --name "$APP_NAME" \
      --paths src \
      --collect-data chaff_generator \
      --add-data "frontend:$SHARE/frontend" \
      "$PKG_DIR/entry.py"
  )

  # The #1 WebEngine packaging failure is a missing helper process.
  local helper
  helper="$(find "$DIST" -type f -name QtWebEngineProcess -print -quit)"
  [[ -n "$helper" ]] || die "QtWebEngineProcess not found under $DIST — \
QtWebEngine was not collected; the AppImage would render a blank window."
  chmod +x "$helper"

  # PyInstaller 6 lands --add-data under _internal/, but the frozen app
  # resolves share/ relative to the bundle root (sys.prefix.parent).
  if [[ -d "$DIST/_internal/$SHARE" && ! -d "$DIST/$SHARE" ]]; then
    mkdir -p "$DIST/share"
    mv "$DIST/_internal/$SHARE" "$DIST/$SHARE"
  fi
  [[ -f "$DIST/$SHARE/frontend/index.html" ]] || die "frontend missing in bundle"

  # the vendored core must actually be importable frozen, data pack and all
  ( cd "$DIST" && APPIMAGE_EXTRACT_AND_RUN=1 QT_QPA_PLATFORM=offscreen \
      ./"$APP_NAME" --selftest-wipe-plan ) \
    || die "frozen --selftest-wipe-plan failed (wipe package or data pack not collected)"
  log "bundle: $(du -sh "$DIST" | cut -f1)"
}

# --- AppDir assembly ----------------------------------------------------------

write_apprun() {
  # linuxdeploy leaves an existing AppRun alone, so ours must be in place
  # before it runs. The Chromium sandbox cannot work inside an AppImage;
  # force it off. The wipe worker re-executes this AppImage as root via
  # pkexec with APPIMAGE_EXTRACT_AND_RUN=1 (see worker_spawn.py).
  cat > "$APPDIR/AppRun" <<'APPRUN'
#!/usr/bin/env bash
HERE="$(dirname "$(readlink -f "$0")")"
export QTWEBENGINE_DISABLE_SANDBOX=1
export QTWEBENGINE_CHROMIUM_FLAGS="${QTWEBENGINE_CHROMIUM_FLAGS:+$QTWEBENGINE_CHROMIUM_FLAGS }--no-sandbox --disable-dev-shm-usage"
exec "$HERE/usr/bin/chafftafarian" "$@"
APPRUN
  chmod +x "$APPDIR/AppRun"
}

assemble_appdir() {
  log "assembling $APPDIR"
  rm -rf "$APPDIR"
  mkdir -p "$APPDIR/usr/bin" \
           "$APPDIR/usr/share/icons/hicolor/512x512/apps" \
           "$APPDIR/usr/share/applications"

  cp -a "$DIST/." "$APPDIR/usr/bin/"
  [[ -x "$APPDIR/usr/bin/$APP_NAME" ]] || die "launcher missing after copy"

  cp "$PKG_DIR/icon-512.png" \
     "$APPDIR/usr/share/icons/hicolor/512x512/apps/$APP_NAME.png"
  cp "$PKG_DIR/icon-512.png" "$APPDIR/$APP_NAME.png"
  cat > "$APPDIR/$APP_NAME.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Chafftafarian
Comment=Chaff generation, NVMe tools, and drive sanitization
Exec=$APP_NAME
Icon=$APP_NAME
Categories=System;Filesystem;Utility;
StartupWMClass=$APP_NAME
Terminal=false
DESKTOP
  write_apprun
}

# --- linuxdeploy ----------------------------------------------------------------

fetch() {  # fetch <url> <dest>; succeeds or leaves nothing behind
  local url="$1" dest="$2"
  [[ -x "$dest" ]] && return 0
  log "downloading $(basename "$dest")"
  if ! curl -fL --retry 2 --connect-timeout 15 -o "$dest" "$url"; then
    rm -f "$dest"
    return 1
  fi
  chmod +x "$dest"
}

deploy() {  # deploy [extra linuxdeploy args...]; returns nonzero on failure
  ( cd "$PKG_DIR" && env APPIMAGE_EXTRACT_AND_RUN=1 \
      "$CACHE/linuxdeploy-x86_64.AppImage" --appdir "$APPDIR" "$@" )
}

run_linuxdeploy() {
  mkdir -p "$CACHE"
  if ! fetch "https://github.com/linuxdeploy/linuxdeploy/releases/latest/download/linuxdeploy-x86_64.AppImage" \
             "$CACHE/linuxdeploy-x86_64.AppImage"; then
    cat >&2 <<TODO

TODO: linuxdeploy could not be downloaded (offline?).
The AppDir is fully assembled at $APPDIR — to finish the AppImage,
download into $CACHE/:
  https://github.com/linuxdeploy/linuxdeploy/releases/latest/download/linuxdeploy-x86_64.AppImage
(chmod +x), then from $PKG_DIR run:
  APPIMAGE_EXTRACT_AND_RUN=1 cache/linuxdeploy-x86_64.AppImage \
    --appdir $APP_NAME.AppDir --output appimage
Continuing without the AppImage.

TODO
    return 1
  fi
  rm -f "$PKG_DIR"/*-x86_64.AppImage

  # no linuxdeploy --plugin qt: it targets system-Qt builds; PyInstaller
  # already bundles all of Qt here (verified by Control Room's history).

  log "running linuxdeploy"
  deploy --output appimage || die "linuxdeploy failed"
  grep -q QTWEBENGINE_DISABLE_SANDBOX "$APPDIR/AppRun" \
    || die "linuxdeploy replaced our AppRun — check the built image"

  local produced appimage="$PKG_DIR/$APP_NAME-x86_64.AppImage"
  produced="$(find "$PKG_DIR" -maxdepth 1 -name '*-x86_64.AppImage' -print -quit)"
  [[ -n "$produced" ]] || die "linuxdeploy reported success but produced no AppImage"
  [[ "$produced" == "$appimage" ]] || mv "$produced" "$appimage"
  printf 'AppImage: %s  (%s)\n' "$appimage" "$(du -h "$appimage" | cut -f1)"
}

# --- post-build smoke -------------------------------------------------------------

smoke() {
  local appimage="$PKG_DIR/$APP_NAME-x86_64.AppImage"
  [[ -f "$appimage" ]] || { echo "SMOKE: skipped (no AppImage)" >&2; return 0; }
  log "smoke: --selftest-wipe-plan"
  "$appimage" --appimage-extract-and-run --selftest-wipe-plan \
    || die "AppImage selftest failed"

  log "smoke: --list-views"
  "$appimage" --appimage-extract-and-run --list-views >/dev/null \
    || die "AppImage --list-views failed"

  log "smoke: --screenshot drives"
  rm -rf "$SMOKE"; mkdir -p "$SMOKE"
  "$appimage" --appimage-extract-and-run \
    --screenshot drives --out "$SMOKE" \
    || die "AppImage screenshot failed"
  local png="$SMOKE/drives.png"
  [[ -s "$png" ]] || die "smoke PNG missing/empty: $png"
  printf 'SMOKE: OK  %s  (%s bytes)\n' "$png" "$(stat -c%s "$png")"
}

main() {
  render_icon
  build_bundle
  assemble_appdir
  run_linuxdeploy || true
  smoke
  log "done"
}

main
