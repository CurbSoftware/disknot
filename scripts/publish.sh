#!/usr/bin/env bash
# Build the Disknot AppImage with the project's package.sh, copy that
# AppImage and the wipe script into the local publish directory, and
# attach them to the GitHub release for the desktop app version.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_NAME="disknot"
DEST="/home/user/projects/published/${REPO_NAME}"
GH_REPO="CurbSoftware/${REPO_NAME}"

cd "$ROOT"

version="$(python3 - <<'PY'
import re
from pathlib import Path
text = Path("generator/apps/desktop/pyproject.toml").read_text()
match = re.search(r'^version = "([^"]+)"', text, re.M)
if not match:
    raise SystemExit("desktop app version not found")
print(match.group(1))
PY
)"
tag="v${version}"

( cd generator/apps/desktop && uv sync --extra dev )
set +e
( cd generator && ./package.sh )
pkg_status=$?
set -e

appimage="generator/packaging/disknot-x86_64.AppImage"
if [[ ! -f "$appimage" ]]; then
  echo "AppImage was not produced (package.sh exit ${pkg_status})" >&2
  exit 1
fi
if [[ "$pkg_status" -ne 0 ]]; then
  echo "package.sh exited ${pkg_status}. Publishing the AppImage it produced." >&2
fi
if [[ ! -f wipe/secure_wipe.sh ]]; then
  echo "wipe script is missing" >&2
  exit 1
fi

mkdir -p "$DEST"
# Leave <tag>/ directories in place. Replace only the current files.
find "$DEST" -mindepth 1 -maxdepth 1 -type f -exec rm -f {} +
cp -a "$appimage" "$DEST/disknot-x86_64.AppImage"
cp -a wipe/secure_wipe.sh "$DEST/secure_wipe.sh"
chmod +x "$DEST/disknot-x86_64.AppImage" "$DEST/secure_wipe.sh"

artifacts=(
  "$DEST/disknot-x86_64.AppImage"
  "$DEST/secure_wipe.sh"
)

notes="$(mktemp)"
cat >"$notes" <<EOF
Disknot ${version}.

The AppImage is the desktop app. secure_wipe.sh is the NVMe wipe script.
EOF

if gh release view "$tag" --repo "$GH_REPO" >/dev/null 2>&1; then
  mapfile -t existing < <(gh release view "$tag" --repo "$GH_REPO" --json assets --jq '.assets[].name')
  for artifact in "${artifacts[@]}"; do
    name="$(basename "$artifact")"
    have_it=0
    for have in "${existing[@]+"${existing[@]}"}"; do
      if [[ "$have" == "$name" ]]; then
        have_it=1
        break
      fi
    done
    if [[ "$have_it" -eq 0 ]]; then
      gh release upload "$tag" "$artifact" --repo "$GH_REPO"
    else
      echo "release $tag already has $name"
    fi
  done
  if printf '%s\n' "${existing[@]+"${existing[@]}"}" | grep -qx 'chafftafarian-x86_64.AppImage'; then
    gh release delete-asset "$tag" chafftafarian-x86_64.AppImage --repo "$GH_REPO" --yes
  fi
else
  gh release create "$tag" "${artifacts[@]}" \
    --repo "$GH_REPO" \
    --target main \
    --title "$tag" \
    --notes-file "$notes"
fi
rm -f "$notes"
