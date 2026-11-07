from utils import stop_process

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import uuid

TMP_DIR = Path(__file__).resolve().parent.parent / 'tmp'

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


@contextmanager
def configure_connections(
  config_file: Path, work_dir: Path, harness_dir: Path, callback_port: int,
  backend: str = 'container'
) -> Iterator[subprocess.Popen[bytes]]:
  """Own both SSH connections and the selected worker for one session."""
  if not config_file.is_file():
    raise RuntimeError('Create env/local.env from env/local.env.example')

  config = read_config(config_file)
  ssh_target = config.get('SSH_TARGET')
  if not ssh_target:
    raise RuntimeError('Set SSH_TARGET in env/local.env')

  if backend == 'utm':
    from vm import vm_worker
    worker = vm_worker(config, work_dir, harness_dir, callback_port)
  elif backend == 'container':
    worker = container_worker(work_dir, harness_dir, callback_port)
  else:
    raise RuntimeError(f'Unknown backend: {backend}')

  with start_ssh_process(ssh_target):
    with worker as process:
      yield process


def run_checked(command: list[str]) -> str:
  return subprocess.run(
    command, check=True, capture_output=True, text=True, timeout=10
  ).stdout.strip()


def ssh_options(key: Path, known_hosts: Path, port: int) -> list[str]:
  return [
    'ssh', '-F', '/dev/null', '-T', '-a', '-x',
    '-i', str(key), '-p', str(port),
    '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
    '-o', 'StrictHostKeyChecking=yes',
    '-o', f'UserKnownHostsFile={known_hosts}',
    '-o', 'GlobalKnownHostsFile=/dev/null',
    '-o', 'HostKeyAlias=harness-container',
    '-o', 'ExitOnForwardFailure=yes', '-o', 'ConnectTimeout=2',
    '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=3',
  ]


@contextmanager
def container_worker(
  work_dir: Path, harness_dir: Path, callback_port: int
) -> Iterator[subprocess.Popen[bytes]]:
  name = f'ai-harness-{uuid.uuid4().hex}'
  ssh_process: subprocess.Popen[bytes] | None = None
  container_process: subprocess.Popen[bytes] | None = None

  def cleanup() -> None:
    if ssh_process is not None:
      stop_process(ssh_process)
    if container_process is not None:
      # Stop the actual container, not just its attached CLI process.
      try:
        subprocess.run(
          ['container', 'stop', '--time', '2', name],
          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
          check=False, timeout=10,
        )
      finally:
        stop_process(container_process)

  TMP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
  with tempfile.TemporaryDirectory(prefix='ai-harness-ssh-', dir=TMP_DIR) as directory:
    session_dir = Path(directory)
    key = session_dir / 'client_key'
    run_checked(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)])
    # The runtime requires directory mounts. Keep the private key outside
    # the mounted directory; expose only the public key to the container.
    public_dir = session_dir / 'public'
    public_dir.mkdir()
    (public_dir / 'client_key.pub').write_bytes(key.with_suffix('.pub').read_bytes())
    # Reserve a candidate loopback port. The runtime reports bind conflicts;
    # no unverified SSH connection is made if another service takes it.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
      reservation.bind(('127.0.0.1', 0))
      ssh_port = reservation.getsockname()[1]

    command = [
      'container', 'run', '--rm', '--name', name, '--progress', 'none',
      '--publish', f'127.0.0.1:{ssh_port}:22',
      '--mount', f'type=bind,source={harness_dir},target=/harness,readonly',
      '--mount', f'type=bind,source={harness_dir.parent / "container"},target=/run/container-config,readonly',
      '--mount', f'type=bind,source={work_dir},target=/work',
      '--mount', f'type=bind,source={public_dir},target=/run/worker-keys,readonly',
      '--workdir', '/work', 'ai-harness:latest',
      '/bin/sh', '/run/container-config/start-sshd.sh',
      '/run/container-config/sshd_config',
    ]
    try:
      container_process = subprocess.Popen(command, stdin=subprocess.DEVNULL)
      deadline = time.monotonic() + 60
      host_key = ''
      while time.monotonic() < deadline:
        if container_process.poll() is not None:
          raise RuntimeError('Container exited before SSH became ready.')
        try:
          host_key = run_checked([
            'container', 'exec', name, 'cat', '/run/sshd/host_key.pub'
          ])
          if host_key:
            break
        except subprocess.CalledProcessError:
          pass
        time.sleep(0.2)
      if not host_key:
        raise RuntimeError('Timed out reading container SSH host key.')
      print(f'{work_dir} mapped to /work', flush=True)
      parts = host_key.split()
      if len(parts) < 2 or parts[0] != 'ssh-ed25519':
        raise RuntimeError('Invalid container SSH host key.')
      known_hosts = session_dir / 'known_hosts'
      known_hosts.write_text(f'harness-container {parts[0]} {parts[1]}\n')
      options = ssh_options(key, known_hosts, ssh_port)
      # Authenticate before opening the worker session. No keyscan or TOFU:
      # the host key above was obtained through the trusted container runtime.
      while time.monotonic() < deadline:
        if container_process.poll() is not None:
          raise RuntimeError('Container exited during SSH startup.')
        probe = subprocess.run(
          [*options, 'root@127.0.0.1', 'true'],
          stdin=subprocess.DEVNULL, capture_output=True, timeout=5,
        )
        if probe.returncode == 0:
          break
        error = probe.stderr.decode('utf-8', errors='replace').strip()
        if error:
          print(error, file=sys.stderr, flush=True)
        time.sleep(0.2)
      else:
        raise RuntimeError('Timed out authenticating to container SSH server.')

      worker_command = shlex.join([
        '/usr/local/bin/python', '-u', '/harness/harness.py',
        '--host-address', '127.0.0.1', '--host-port', '9000',
        '--ollama-url', 'http://127.0.0.1:11435/api/chat',
      ])
      ssh_process = subprocess.Popen([
        *options,
        '-R', f'127.0.0.1:9000:127.0.0.1:{callback_port}',
        '-R', '127.0.0.1:11435:127.0.0.1:11435',
        'root@127.0.0.1', worker_command,
      ], stdin=subprocess.DEVNULL)
      yield ssh_process
    finally:
      cleanup()
