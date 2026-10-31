#!/usr/bin/env bash

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# Make sure the `src` folder is mounted as readonly
container run -it --rm \
  --mount "type=bind,source=$SCRIPT_DIR/src,target=/harness,readonly" \
  --mount "type=bind,source=$SCRIPT_DIR/tmp,target=/work" \
  --workdir /work \
  ai-harness:latest python /harness/harness.py
