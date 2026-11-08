#!/usr/bin/env bash

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if (($# != 0)); then
  printf 'Usage: %s\n' "$0" >&2
  exit 2
fi

exec python3 "$SCRIPT_DIR/host/host.py"
