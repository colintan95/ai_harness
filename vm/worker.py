"""Guest supervisor: mount the project and tie worker lifetime to SSH stdin."""
import argparse
import os
from pathlib import Path
import select
import signal
import subprocess
import sys


def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument('--work-dir', type=Path, required=True)
  parser.add_argument('--project-relative', type=Path, default=Path('.'))
  args = parser.parse_args()
  # SSH may deliver a hangup when its connection closes. Exit through finally.
  def stop(signum: int, frame: object) -> None:
    raise SystemExit(128 + signum)

  for signum in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
    signal.signal(signum, stop)
  workspace = args.work_dir.expanduser().resolve()
  workspace.mkdir(parents=True, exist_ok=True)
  if os.path.ismount(workspace) or any(workspace.iterdir()):
    raise RuntimeError(f'Workspace mount directory must be empty and unmounted: {workspace}')

  # Modern UTM exposes a named folder inside the macOS automounted volume.
  # Keep a stable workspace path without requiring privileged remounts.
  automatic_share = Path('/Volumes/My Shared Files')
  linked = False
  mounted = False
  if os.path.ismount(automatic_share):
    shares = [entry for entry in automatic_share.iterdir()
              if entry.is_dir()
              and entry.name not in {'.Spotlight-V100', '.Trashes', '.fseventsd'}]
    if len(shares) != 1 or not shares[0].is_dir():
      raise RuntimeError('Expected exactly one project in /Volumes/My Shared Files')
    workspace.rmdir()  # Validated empty above.
    try:
      workspace.symlink_to(shares[0], target_is_directory=True)
    except OSError:
      workspace.mkdir()
      raise
    linked = True
  else:
    # Older UTM versions expose a single directory with the literal share tag.
    subprocess.run(['/sbin/mount_virtiofs', 'share', str(workspace)], check=True)
    mounted = True
  process: subprocess.Popen[bytes] | None = None
  try:
    if not (linked or os.path.ismount(workspace)) or not os.access(workspace, os.W_OK):
      raise RuntimeError('Workspace share is not mounted and writable')
    project = (workspace / args.project_relative).resolve()
    if not project.is_relative_to(workspace.resolve()) or not project.is_dir():
      raise RuntimeError('Requested project must be a directory inside the shared workspace')
    env = os.environ.copy()
    env['PATH'] = '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'
    process = subprocess.Popen([
      sys.executable, '-u', str(Path(__file__).with_name('harness.py')),
      '--work-dir', str(project), '--host-port', '9000',
    ], env=env, stdin=subprocess.DEVNULL, start_new_session=True)
    while process.poll() is None:
      readable, _, _ = select.select([sys.stdin], [], [], 0.5)
      if readable and not os.read(sys.stdin.fileno(), 4096):
        break
  finally:
    if process is not None:
      try:
        os.killpg(process.pid, signal.SIGTERM)
      except ProcessLookupError:
        pass
      try:
        process.wait(timeout=5)
      except subprocess.TimeoutExpired:
        pass
      # Also terminate descendants if the harness itself has already exited.
      try:
        os.killpg(process.pid, signal.SIGKILL)
      except ProcessLookupError:
        pass
      process.wait()
    if linked:
      workspace.unlink()
      workspace.mkdir()
    elif mounted:
      subprocess.run(['/sbin/umount', str(workspace)], check=True, timeout=10)


if __name__ == '__main__':
  main()
