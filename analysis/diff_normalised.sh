#!/usr/bin/env bash
set -euo pipefail

# Diff while ignoring length, uncompressed_length, and is_compressed
# so we can just see the different libraries

cd "$(dirname "$0")"

tmp1="$(mktemp)"
tmp2="$(mktemp)"
trap 'rm -f "$tmp1" "$tmp2"' EXIT

sed -E 's/[-+]?[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?,/<NUM>,/g' "./main-linux-arm64-no_position.txt" > "$tmp1"
sed -E 's/[-+]?[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?,/<NUM>,/g' "./main-linux-arm64-working-no_position.txt" > "$tmp2"

# diff exits 1 when files differ; that's fine. Only fail on >1.
set +e
diff -u "$tmp1" "$tmp2" > "./diff-normalized.txt"
st=$?
set -e
if [ "$st" -gt 1 ]; then
  echo "diff failed with status $st" >&2
  exit "$st"
fi
echo "Wrote ./diff-normalized.txt (diff exit $st)"