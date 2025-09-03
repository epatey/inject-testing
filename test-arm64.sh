#!/bin/bash
set -euox pipefail

docker run \
 --platform linux/arm64 \
 --rm -it \
 -v "$PWD/dist:/dist" \
 debian:bookworm-slim \
 bash -lc '/dist/main-linux-arm64'
