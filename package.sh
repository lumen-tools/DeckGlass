#!/bin/bash
# Build an installable Decky zip:
#   ./package.sh <path-to-libretro-slang-shaders> [path-to-unpacked-retro-crisis-release]
# The second path is the folder that contains "retro crisis/".
set -euo pipefail
cd "$(dirname "$0")"
NAME=$(python3 -c 'import json;print(json.load(open("plugin.json"))["name"])')
VER=$(python3 -c 'import json;print(json.load(open("package.json"))["version"])')
STAGE=$(mktemp -d)/$NAME
mkdir -p "$STAGE/shaders"
pnpm run build >/dev/null
cp -r dist main.py plugin.json package.json LICENSE README.md "$STAGE/"
rm -f "$STAGE/dist/"*.map
cp -r slangfx "$STAGE/"
cp -r "$1" "$STAGE/shaders/shaders_slang"
rm -rf "$STAGE/shaders/shaders_slang/"{.git,.github,bezel}
if [ $# -ge 2 ]; then
  mkdir -p "$STAGE/shaders/rc"
  cp -r "$2/retro crisis" "$STAGE/shaders/rc/"
  [ -f "$2/LICENSE" ] && cp "$2/LICENSE" "$STAGE/shaders/rc/LICENSE"
fi
find "$STAGE/shaders" \( -name '__MACOSX' -o -name '._*' -o -name '.DS_Store' \) -prune -exec rm -rf {} +
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} +
chmod 755 "$STAGE/slangfx/bin/"*
mkdir -p out
rm -f "out/$NAME-$VER.zip"
(cd "$(dirname "$STAGE")" && zip -qr9 - "$NAME") > "out/$NAME-$VER.zip"
ls -la "out/$NAME-$VER.zip"
