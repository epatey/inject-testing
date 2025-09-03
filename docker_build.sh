#!/usr/bin/env bash
set -euo pipefail

# Reproducible builder for PyInstaller binary using Docker.
# Usage:
#   ./docker_build.sh [platform] [tag]
# Examples:
#   ./docker_build.sh                   # builds linux/amd64, tag script-builder:latest
#   ./docker_build.sh linux/arm64 v1    # builds arm64, tag script-builder:v1

PLATFORM="${1:-linux/amd64}"
TAG="${2:-script-builder:latest}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="$SCRIPT_DIR/dist"

mkdir -p "$DIST_DIR"

echo "[1/3] Building image for $PLATFORM as $TAG"
docker buildx build \
  --platform "$PLATFORM" \
  -f "$SCRIPT_DIR/build.Dockerfile" \
  -t "$TAG" \
  "$SCRIPT_DIR" \
  --load

echo "[2/3] Creating container to extract artifact"
CID=$(docker create "$TAG")
trap 'docker rm -f "$CID" >/dev/null 2>&1 || true' EXIT

echo "[3/3] Copying /out/main to dist/"
OUT_NAME="main-$(echo "$PLATFORM" | tr '/' '-')"
docker cp "$CID:/out/main" "$DIST_DIR/$OUT_NAME"
chmod +x "$DIST_DIR/$OUT_NAME"
echo "Built: $DIST_DIR/$OUT_NAME"