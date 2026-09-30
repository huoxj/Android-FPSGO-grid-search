#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# 1. Download dependencies
# perfetto trace processor shell
TP_DIR="vendor/perfetto"
TP_BIN="$TP_DIR/tp_shell_bin"
TP_URL="https://commondatastorage.googleapis.com/perfetto-luci-artifacts/v57.2/linux-amd64/trace_processor_shell"
TP_SHA256="55ba613fc6d4f71df81eee2dbfc293020063655c241b3e314bff75345b802684"
mkdir -p "$TP_DIR"
if [ ! -f "$TP_BIN" ]; then
  if ! curl -fL --retry 3 -o "$TP_BIN" "$TP_URL"; then
    echo "Download failed, try manually download $TP_URL to $TP_BIN:"
    exit 1
  fi
fi
echo "$TP_SHA256  $TP_BIN" | sha256sum -c - || { echo "sha256 checksum failed"; exit 1; }
chmod +x "$TP_BIN"

# 2. Build executable
NAME=fpsgo-optim
VERSION=$(git describe --tags --always 2>/dev/null || echo dev)
TARGET="$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m)"

rm -rf dist build .pyarmor/pack main.spec
uv run pyarmor gen --pack onefile -O dist src/main.py

uv run pyinstaller --onefile --name "$NAME" \
  --additional-hooks-dir .pyarmor/pack \
  --collect-data perfetto \
  .pyarmor/pack/dist/main.py

# 3. Create Package
PKG="dist/${NAME}-${VERSION}-${TARGET}"
rm -rf "$PKG" && mkdir -p "$PKG"
mv "dist/$NAME" "$PKG/$NAME"
cp -r resources perfetto_configs docs "$PKG/"
cp config.example.toml "$PKG/config.toml"
cp README.md LICENSE "$PKG/"

mkdir -p "$PKG/vendor/perfetto"
cp "$TP_BIN" "$PKG/vendor/perfetto/tp_shell_bin"

(cd dist && zip -rq "$(basename "$PKG").zip" "$(basename "$PKG")")
echo "done: dist/$(basename "$PKG").zip"

