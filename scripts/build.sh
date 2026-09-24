#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

NAME=fpsgo-optim
VERSION=$(git describe --tags --always 2>/dev/null || echo dev)
TARGET="$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m)"

rm -rf dist build .pyarmor/pack
uv run pyarmor gen --pack onefile -O dist src/main.py

mv dist/main "dist/$NAME"

PKG="dist/${NAME}-${VERSION}-${TARGET}"
rm -rf "$PKG" && mkdir -p "$PKG"
mv "dist/$NAME" "$PKG/$NAME"
cp -r resources perfetto_configs docs "$PKG/"
cp config.example.toml "$PKG/config.toml"
cp README.md LICENSE "$PKG/"

(cd dist && zip -rq "$(basename "$PKG").zip" "$(basename "$PKG")")
echo "done: dist/$(basename "$PKG").zip"

