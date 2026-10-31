#!/usr/bin/env bash

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

container system start
container build -t ai-harness:latest "$SCRIPT_DIR"
