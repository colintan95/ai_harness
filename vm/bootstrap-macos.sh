#!/bin/bash
set -euo pipefail

# Run inside the guest as the account that will run the harness.
# bash bootstrap-macos.sh /absolute/path/to/python3
PYTHON="${1:?Provide the absolute Python 3.12+ executable path}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/.local/share/ai-harness"
WORK_DIR="$HOME/harness-work"

if [[ "$(uname -s)" != Darwin || "$EUID" == 0 ]]; then
  echo 'Run this inside the macOS guest as the harness user, without sudo.' >&2
  exit 1
fi
if [[ "$PYTHON" != /* ]]; then
  echo 'Python path must be absolute.' >&2
  exit 1
fi
"$PYTHON" - <<'PY'
import sys
if sys.version_info < (3, 12):
    raise SystemExit('Python 3.12 or later is required')
PY

test -r "$SCRIPT_DIR/../harness/harness.py"
test -r "$SCRIPT_DIR/../container/requirements.txt"
mkdir -p "$INSTALL_DIR" "$WORK_DIR"
chmod 700 "$INSTALL_DIR"
cp "$SCRIPT_DIR/../harness/harness.py" "$INSTALL_DIR/harness.py"
cp "$SCRIPT_DIR/worker.py" "$INSTALL_DIR/worker.py"
cp "$SCRIPT_DIR/../container/requirements.txt" "$INSTALL_DIR/requirements.txt"
"$PYTHON" -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt"

printf '\nBootstrap complete. Host configuration:\n'
printf 'VM_SSH_TARGET=%s@%s.local\n' "$(id -un)" "$(scutil --get LocalHostName)"
printf 'VM_PYTHON="%s/venv/bin/python"\n' "$INSTALL_DIR"
printf 'VM_INSTALL_DIR="%s"\n' "$INSTALL_DIR"
printf 'VM_WORK_DIR="%s"\n' "$WORK_DIR"
