#!/bin/bash
set -euox pipefail

docker run --rm -it -v "$PWD/dist:/dist" debian:bookworm-slim bash -lc '/dist/main'
