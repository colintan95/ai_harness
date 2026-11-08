"""Manage the host's SSH connections and UTM VM worker for a session."""
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Iterator
import fcntl
import shlex
import subprocess
import sys
import tempfile
import time

TMP_DIR = Path(__file__).resolve().parent.parent / 'tmp'
SSH_TIMEOUT_SECONDS = 300

# Arguments are passed separately, never interpolated into AppleScript source.
INSPECT_VM = '''on run argv
  local harnessVMRef, harnessVMMatches, harnessShares, harnessSharePaths
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
    if (count of harnessShares) is not 2 then error "Configure exactly two shared directories in UTM"
    set harnessSharePaths to ""
    repeat with harnessShare in harnessShares
      set harnessSharePaths to harnessSharePaths & (POSIX path of harnessShare) & linefeed
    end repeat
    return harnessSharePaths
  end tell
end run'''


def stop_process(process: subprocess.Popen[Any]) -> None:
  if process.poll() is None:
    process.terminate()
    try:
      process.wait(timeout=5)
    except subprocess.TimeoutExpired:
      process.kill()
      process.wait()


def read_config(config_file: Path) -> dict[str, str]:
  """Read literal assignments without executing shell code or expanding variables."""
  values: dict[str, str] = {}
  for line_number, line in enumerate(config_file.read_text().splitlines(), 1):
    assignment = line.strip()
    if not assignment or assignment.startswith('#'):
      continue
    if assignment.startswith('export '):
      assignment = assignment[7:].lstrip()
    name, separator, value = assignment.partition('=')
    if not separator:
      raise RuntimeError(f'Invalid assignment at {config_file}:{line_number}')
    try:
      parts = shlex.split(value, comments=True)
    except ValueError as error:
      raise RuntimeError(f'Invalid value at {config_file}:{line_number}') from error
    if len(parts) > 1:
      raise RuntimeError(f'Quote values containing spaces at {config_file}:{line_number}')
    values[name.strip()] = parts[0] if parts else ''
  return values


@contextmanager
def start_ssh_process(ssh_target: str) -> Iterator[None]:
  TMP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
  with tempfile.TemporaryDirectory(prefix='harness-ollama-', dir=TMP_DIR) as directory:
    control_socket = str(Path(directory) / 'control')
    # Forward only host loopback to the remote Ollama service.
    process = subprocess.Popen([
      'ssh', '-N', '-a', '-M', '-S', control_socket,
      '-o', 'ExitOnForwardFailure=yes',
      '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
      '-L', '127.0.0.1:11435:127.0.0.1:11434', ssh_target
    ], stdin=subprocess.DEVNULL)
    try:
      deadline = time.monotonic() + 15
      while time.monotonic() < deadline:
        if process.poll() is not None:
          raise RuntimeError('Ollama SSH tunnel exited during startup.')
        # The master becomes available after authentication and forward setup.
        status = subprocess.run(
          ['ssh', '-S', control_socket, '-O', 'check', ssh_target],
          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
          stderr=subprocess.DEVNULL, check=False, timeout=2,
        )
        if status.returncode == 0 and process.poll() is None:
          break
        time.sleep(0.1)
      else:
        raise RuntimeError('Timed out starting Ollama SSH tunnel.')
      yield
    finally:
      stop_process(process)


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


def guest_home(options: list[str], target: str) -> PurePosixPath:
  result = subprocess.run(
    [*options, target, 'printf "%s\\n" "$HOME"'],
    stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True,
    timeout=10,
  )
  value = result.stdout.strip()
  if not value.startswith('/') or len(value.splitlines()) != 1:
    raise RuntimeError('Could not determine an absolute VM home directory')
  return PurePosixPath(value)


@contextmanager
def vm_worker(
  config: dict[str, str], work_dir: Path, harness_dir: Path, callback_port: int,
) -> Iterator[subprocess.Popen[bytes]]:
  work_dir = work_dir.resolve()
  identifier = required(config, 'VM_ID')
  target = required(config, 'VM_SSH_TARGET')
  if target.startswith('-'):
    raise RuntimeError('VM_SSH_TARGET cannot start with a dash')
  options = guest_options(config)
  if Path(config['VM_SSH_KEY']).expanduser().resolve().is_relative_to(work_dir):
    raise RuntimeError('VM_SSH_KEY must be outside the shared workspace')
  # Serialize UTM sessions, including startup, to protect fixed guest ports/share.
  lock_dir = harness_dir.parent / 'tmp'
  lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
  with (lock_dir / 'utm.lock').open('a') as lock:
    try:
      fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
      raise RuntimeError('Another UTM harness session is already running') from error
    configured_shares = {
      Path(path).resolve()
      for path in applescript(INSPECT_VM, identifier).splitlines()
      if path
    }
    expected_shares = {harness_dir.resolve(), work_dir}
    if configured_shares != expected_shares:
      raise RuntimeError(
        'Configure UTM to share exactly '
        f'{harness_dir.resolve()} and {work_dir}'
      )
    process: subprocess.Popen[bytes] | None = None
    try:
      print(f'Connecting to running UTM VM {identifier}...', flush=True)
      print(f'Waiting up to {SSH_TIMEOUT_SECONDS}s for SSH to {target}. Unlock the guest if needed.', flush=True)
      deadline = time.monotonic() + SSH_TIMEOUT_SECONDS
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

      home = guest_home(options, target)
      install_dir = home / '.local' / 'share' / 'ai-harness'
      python = install_dir / 'venv' / 'bin' / 'python'
      mount_dir = home / 'work'

      print('Guest SSH is ready; updating harness code...', flush=True)
      # Update the job code through SSH, outside the shared workspace. The
      # LaunchAgent supervisor and its venv are installed by guest bootstrap.
      source = harness_dir / 'harness.py'
      destination = str(Path(install_dir) / source.name)
      command = f'umask 077; cat > {shlex.quote(destination)}'
      subprocess.run(
        [*options, target, command], input=source.read_bytes(),
        check=True, timeout=30,
      )

      launcher = str(Path(install_dir) / 'launcher.py')
      ready = subprocess.run(
        [*options, target, shlex.join([str(python), '-u', launcher, 'ping'])],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15,
      )
      if ready.returncode != 0:
        details = ready.stderr.strip() or ready.stdout.strip()
        raise RuntimeError(
          'The GUI LaunchAgent is not ready. Log into the VM desktop and run '
          f'harness/bootstrap.sh there. {details}'
        )

      command = shlex.join([
        str(python), '-u', launcher, 'run', '--project-relative', '.',
      ])
      print('Starting GUI-session worker and reverse tunnels...', flush=True)
      process = subprocess.Popen([
        *options,
        '-R', f'127.0.0.1:9000:127.0.0.1:{callback_port}',
        '-R', '127.0.0.1:11435:127.0.0.1:11435',
        target, command,
      ], stdin=subprocess.PIPE)
      print(f'{work_dir} available in VM at {mount_dir}', flush=True)
      yield process
    finally:
      if process is not None:
        # EOF makes the SSH-side client disconnect from the GUI LaunchAgent;
        # the agent closes worker stdin so the harness removes its workspace link.
        if process.stdin is not None:
          process.stdin.close()
        try:
          process.wait(timeout=15)
        except subprocess.TimeoutExpired:
          stop_process(process)


@contextmanager
def configure_connections(
  config_file: Path, work_dir: Path, harness_dir: Path, callback_port: int,
) -> Iterator[subprocess.Popen[bytes]]:
  """Own the SSH connections and VM worker for one session."""
  if not config_file.is_file():
    raise RuntimeError('Create env/local.env from env/local.env.example')

  config = read_config(config_file)
  ssh_target = config.get('SSH_TARGET')
  if not ssh_target:
    raise RuntimeError('Set SSH_TARGET in env/local.env')

  worker = vm_worker(config, work_dir, harness_dir, callback_port)
  with start_ssh_process(ssh_target):
    with worker as process:
      yield process
