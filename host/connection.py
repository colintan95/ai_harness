from utils import stop_process

from pathlib import Path
import atexit
import shlex
import subprocess
import sys

DNS_DOMAIN = 'host.container.internal'
DNS_LOCALHOST = '203.0.113.113'

def read_ssh_target(config_file: Path) -> str | None:
  try:
    lines = config_file.read_text().splitlines()
  except OSError as error:
    print(f'Error reading {config_file}: {error}', file=sys.stderr)
    return None

  for line in lines:
    assignment = line.strip()
    if assignment.startswith('export '):
      assignment = assignment[7:].lstrip()

    name, separator, value = assignment.partition('=')
    if not separator or name.strip() != 'SSH_TARGET':
      continue

    try:
      parts = shlex.split(value, comments=True)
    except ValueError as error:
      print(f'Invalid SSH_TARGET in {config_file}: {error}', file=sys.stderr)
      return None

    if parts:
      return parts[0]

    return None

  return None

def configure_container_dns() -> bool:
  if dns_is_configured():
    print('Container access to host network is already configured.')
    return True

  print('Configuring container access to host network...')
  try:
    subprocess.run([
      'sudo', 'container', 'system', 'dns', 'create', DNS_DOMAIN,
      '--localhost', DNS_LOCALHOST
    ], check=False)
  except OSError as error:
    print(f'Error running sudo: {error}', file=sys.stderr)
    return False

  if dns_is_configured():
    return True

  print('Error configuring container DNS.', file=sys.stderr)
  return False

def dns_is_configured() -> bool:
  try:
    result = subprocess.run(
      ['container', 'system', 'dns', 'list', '--quiet'],
      capture_output=True,
      text=True,
      check=True
    )
  except (OSError, subprocess.CalledProcessError):
    return False

  return DNS_DOMAIN in {line.strip() for line in result.stdout.splitlines()}

def start_ssh_process(ssh_target: str) -> bool:
  print('Starting SSH tunnel on port 11435...')
  try:
    process = subprocess.Popen([
      'ssh', '-N', '-L', '11435:127.0.0.1:11434', ssh_target
    ], stdin=subprocess.DEVNULL)
  except OSError as error:
    print(f'Error running ssh: {error}', file=sys.stderr)
    return False

  atexit.register(stop_process, process)
  return True

def configure_connections(config_file: Path) -> bool:
  if not config_file.is_file():
    print('Create env/local.env from env/local.env.example', file=sys.stderr)
    return False

  ssh_target = read_ssh_target(config_file)
  if not ssh_target:
    print('Set SSH_TARGET in env/local.env', file=sys.stderr)
    return False

  if not configure_container_dns():
    return False

  return start_ssh_process(ssh_target)
