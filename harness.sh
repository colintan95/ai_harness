#!/usr/bin/env bash

# Details:
# - Run with stdin with the `-it` flag
# - Mounts the `src` directory as `readonly` - so the agent can't modify the
#     harness code
# - Mounts `src` as `/harness` in the container
container run -it --rm \
  --mount "type=bind,source=$PWD/src,target=/harness,readonly" \
  --workdir /harness \
  ai-harness:latest python harness.py
