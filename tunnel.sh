#!/usr/bin/env bash

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/env/local.env"

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "Create local.env from local.env.example" >&2
  exit 1
fi

source "$CONFIG_FILE"
: "${SSH_TARGET:?Set SSH_TARGET in tunnel.local.env}"

echo "Configuring container access to host SSH tunnel..."

# Redirects the container traffic to host 127.0.0.1
# Use `host.container.internal` in the container instead of the ip address
sudo container system dns create host.container.internal \
  --localhost 203.0.113.113

echo "Starting SSH tunnel on port 11435..."

# Create the ssh tunnel to the ollama server - will keep running in the
# foreground
ssh -N -L 11435:127.0.0.1:11434 "$SSH_TARGET"