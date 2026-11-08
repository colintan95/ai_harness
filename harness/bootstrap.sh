#!/bin/bash
set -euo pipefail

# Run inside the guest as the account that will run the harness.
# bash bootstrap.sh /absolute/path/to/python3
PYTHON="${1:?Provide the absolute Python 3.12+ executable path}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/.local/share/ai-harness"
WORK_DIR="$HOME/work"
LAUNCH_AGENT_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/AIHarness"
AGENT_LABEL='com.ai-harness.desktop'
PLIST_PATH="$LAUNCH_AGENT_DIR/$AGENT_LABEL.plist"

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

test -r "$SCRIPT_DIR/harness.py"
test -r "$SCRIPT_DIR/requirements.txt"
mkdir -p "$INSTALL_DIR" "$WORK_DIR" "$LAUNCH_AGENT_DIR" "$LOG_DIR"
chmod 700 "$INSTALL_DIR"
cp "$SCRIPT_DIR/harness.py" "$INSTALL_DIR/harness.py"
cp "$SCRIPT_DIR/service.py" "$INSTALL_DIR/service.py"
cp "$SCRIPT_DIR/launcher.py" "$INSTALL_DIR/launcher.py"
cp "$SCRIPT_DIR/permissions.py" "$INSTALL_DIR/permissions.py"
cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/requirements.txt"
"$PYTHON" -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt"

VENV_PYTHON="$INSTALL_DIR/venv/bin/python"
"$PYTHON" - "$INSTALL_DIR" "$WORK_DIR" "$PLIST_PATH" "$AGENT_LABEL" "$LOG_DIR" <<'PY'
import plistlib
import sys
from pathlib import Path

install_dir = Path(sys.argv[1])
work_dir = Path(sys.argv[2])
plist_path = Path(sys.argv[3])
label = sys.argv[4]
log_dir = Path(sys.argv[5])
python = install_dir / 'venv' / 'bin' / 'python'
plist = {
    'Label': label,
    'ProgramArguments': [
        str(python), '-u', str(install_dir / 'service.py'),
        '--install-dir', str(install_dir), '--work-dir', str(work_dir),
    ],
    'WorkingDirectory': str(install_dir),
    'EnvironmentVariables': {
        'HOME': str(Path.home()),
        'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
    },
    'RunAtLoad': True,
    'KeepAlive': True,
    'StandardOutPath': str(log_dir / 'agent.log'),
    'StandardErrorPath': str(log_dir / 'agent.log'),
}
with plist_path.open('wb') as output:
    plistlib.dump(plist, output)
PY
chmod 600 "$PLIST_PATH"

# Run this bootstrap from Terminal in the guest so the job is loaded into the
# logged-in user's GUI launchd domain, not the background SSH domain.
launchctl bootout "gui/$UID" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$UID" "$PLIST_PATH"

for _ in {1..50}; do
  if [[ -S "$HOME/Library/Caches/ai-harness/agent.sock" ]]; then
    break
  fi
  sleep 0.1
done
if [[ ! -S "$HOME/Library/Caches/ai-harness/agent.sock" ]]; then
  echo "LaunchAgent did not start; inspect $LOG_DIR/agent.log" >&2
  exit 1
fi

printf '\nBootstrap complete. Host configuration:\n'
printf 'VM_SSH_TARGET=%s@%s.local\n' "$(id -un)" "$(scutil --get LocalHostName)"
printf 'Guest install directory: %s\n' "$INSTALL_DIR"
printf 'Guest workspace directory: %s\n' "$WORK_DIR"
printf 'LaunchAgent %s is running in the logged-in GUI session.\n' "$AGENT_LABEL"
printf 'If a permission request was not shown, enable Screen Recording, Accessibility, and mouse/keyboard event access for %s in System Settings.\n' "$VENV_PYTHON"
