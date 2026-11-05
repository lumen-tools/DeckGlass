#!/bin/bash
# Build an installable Decky zip: ./package.sh <path-to-slang-shaders> [path-to-retro-crisis]
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
cp -r "$1" "$STAGE/shaders/slang-shaders"
rm -rf "$STAGE/shaders/slang-shaders/"{.git,.github,bezel}
if [ $# -ge 2 ]; then cp -r "$2" "$STAGE/shaders/retro-crisis"; rm -rf "$STAGE/shaders/retro-crisis/.git"; fi
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} +
chmod 755 "$STAGE/slangfx/bin/"*
mkdir -p out
rm -f "out/$NAME-$VER.zip"
(cd "$(dirname "$STAGE")" && zip -qr9 - "$NAME") > "out/$NAME-$VER.zip"
ls -la "out/$NAME-$VER.zip"
