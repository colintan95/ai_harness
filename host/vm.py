"""One project share and one SSH worker in a dedicated macOS UTM VM."""
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Iterator
import fcntl
import shlex
import subprocess
import sys
import time

from utils import stop_process

# Arguments are passed separately, never interpolated into AppleScript source.
INSPECT_VM = '''on run argv
  local harnessVMRef, harnessVMMatches, harnessShares, harnessShareFolder
  tell application "UTM"
    set harnessVMMatches to virtual machines whose id is (item 1 of argv)
    if (count of harnessVMMatches) is 0 then
      set harnessVMMatches to virtual machines whose name is (item 1 of argv)
    end if
    if (count of harnessVMMatches) is not 1 then error "VM name or UUID must identify one VM"
    set harnessVMRef to item 1 of harnessVMMatches
    if backend of harnessVMRef is not apple then error "A macOS Apple backend VM is required"
    if status of harnessVMRef is not started then error "Start and unlock the harness VM in UTM before connecting"
    set harnessShares to registry of harnessVMRef
    if (count of harnessShares) is not 1 then error "Configure exactly one shared directory in UTM"
    set harnessShareFolder to item 1 of harnessShares
    return POSIX path of harnessShareFolder
  end tell
end run'''


def applescript(source: str, *arguments: str) -> str:
  try:
    return subprocess.run(
      ['osascript', '-e', source, *arguments], check=True,
      capture_output=True, text=True, timeout=30,
    ).stdout.strip()
  except subprocess.CalledProcessError as error:
    raise RuntimeError(f'UTM automation failed: {error.stderr.strip()}') from error


def required(config: dict[str, str], name: str) -> str:
  value = config.get(name, '')
  if not value:
    raise RuntimeError(f'Set {name} in env/local.env')
  return value


def guest_options(config: dict[str, str]) -> list[str]:
  key = Path(required(config, 'VM_SSH_KEY')).expanduser().resolve()
  if not key.is_file():
    raise RuntimeError(f'Missing SSH private key: {key}')
  # Honor normal OpenSSH config and known_hosts, with an explicit VM identity.
  return [
    'ssh', '-i', str(key), '-T', '-a', '-x',
    '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
    '-o', 'ExitOnForwardFailure=yes', '-o', 'ConnectTimeout=3',
    '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=3',
  ]


@contextmanager
def vm_worker(
  config: dict[str, str], work_dir: Path, harness_dir: Path, callback_port: int,
) -> Iterator[subprocess.Popen[bytes]]:
  work_dir = work_dir.resolve()
  identifier = required(config, 'VM_ID')
  target = required(config, 'VM_SSH_TARGET')
  if target.startswith('-'):
    raise RuntimeError('VM_SSH_TARGET cannot start with a dash')
  python = required(config, 'VM_PYTHON')
  install_dir = required(config, 'VM_INSTALL_DIR')
  mount_dir = required(config, 'VM_WORK_DIR')
  if any(not path.startswith('/') for path in (python, install_dir, mount_dir)):
    raise RuntimeError('Guest Python, install, and workspace paths must be absolute')
  if (mount_dir == '/'
      or PurePosixPath(install_dir).is_relative_to(PurePosixPath(mount_dir))
      or '..' in PurePosixPath(mount_dir).parts):
    raise RuntimeError('Use a dedicated workspace mount separate from the installation')
  options = guest_options(config)
  if Path(config['VM_SSH_KEY']).expanduser().resolve().is_relative_to(work_dir):
    raise RuntimeError('VM_SSH_KEY must be outside the shared workspace')
  try:
    ssh_timeout = int(config.get('VM_SSH_TIMEOUT', '300'))
  except ValueError as error:
    raise RuntimeError('VM_SSH_TIMEOUT must be a positive integer') from error
  if ssh_timeout <= 0:
    raise RuntimeError('VM_SSH_TIMEOUT must be a positive integer')
  vm_dir = harness_dir.parent / 'vm'
  # Serialize UTM sessions, including startup, to protect fixed guest ports/share.
  lock_dir = harness_dir.parent / 'tmp'
  lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
  with (lock_dir / 'utm.lock').open('a') as lock:
    try:
      fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
      raise RuntimeError('Another UTM harness session is already running') from error
    shared_root = Path(applescript(INSPECT_VM, identifier)).resolve()
    try:
      project_relative = work_dir.relative_to(shared_root)
    except ValueError as error:
      raise RuntimeError(
        f'{work_dir} is outside the VM shared folder {shared_root}. '
        'Choose a project within the existing share, or change the share in UTM.'
      ) from error
    process: subprocess.Popen[bytes] | None = None
    try:
      print(f'Connecting to running UTM VM {identifier}...', flush=True)
      print(f'Waiting up to {ssh_timeout}s for SSH to {target}. Unlock the guest if needed.', flush=True)
      deadline = time.monotonic() + ssh_timeout
      next_report = time.monotonic()
      last_error = ''
      while time.monotonic() < deadline:
        try:
          probe = subprocess.run(
            [*options, target, 'true'], stdin=subprocess.DEVNULL,
            capture_output=True, timeout=8,
          )
          if probe.returncode == 0:
            break
          last_error = probe.stderr.decode(errors='replace').strip()
        except subprocess.TimeoutExpired:
          last_error = 'SSH probe timed out; check guest hostname and networking'
        if any(message in last_error for message in (
          'REMOTE HOST IDENTIFICATION HAS CHANGED',
          'Host key verification failed', 'Permission denied',
          'UNPROTECTED PRIVATE KEY FILE', 'incorrect passphrase',
        )):
          raise RuntimeError(f'VM SSH failed: {last_error}')
        if time.monotonic() >= next_report:
          print(f'Waiting for guest SSH: {last_error or "connection not ready"}',
                file=sys.stderr, flush=True)
          next_report = time.monotonic() + 10
        time.sleep(1)
      else:
        raise RuntimeError(f'Timed out waiting for VM SSH: {last_error}')

      print('Guest SSH is ready; updating harness code...', flush=True)
      # Update code through SSH, outside the shared workspace. Dependencies are
      # installed explicitly by bootstrap, never silently changed each session.
      for source in (harness_dir / 'harness.py', vm_dir / 'worker.py'):
        destination = str(Path(install_dir) / source.name)
        command = f'umask 077; cat > {shlex.quote(destination)}'
        subprocess.run(
          [*options, target, command], input=source.read_bytes(),
          check=True, timeout=30,
        )

      command = shlex.join([
        python, '-u', str(Path(install_dir) / 'worker.py'),
        '--work-dir', mount_dir, '--project-relative', project_relative.as_posix(),
      ])
      print('Starting guest harness and reverse tunnels...', flush=True)
      process = subprocess.Popen([
        *options,
        '-R', f'127.0.0.1:9000:127.0.0.1:{callback_port}',
        '-R', '127.0.0.1:11435:127.0.0.1:11435',
        target, command,
      ], stdin=subprocess.PIPE)
      print(f'{work_dir} available in VM at {PurePosixPath(mount_dir) / project_relative.as_posix()}', flush=True)
      yield process
    finally:
      if process is not None:
        # EOF tells the supervisor to terminate the guest process group and
        # unmount, even when the harness is busy in a model request or command.
        if process.stdin is not None:
          process.stdin.close()
        try:
          process.wait(timeout=15)
        except subprocess.TimeoutExpired:
          stop_process(process)
