from ddgs import DDGS

from pathlib import Path
from types import FrameType
from typing import Any, TextIO
import atexit
import json
import shlex
import signal
import subprocess
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
CONFIG_FILE = REPO_DIR / 'env' / 'local.env'

HARNESS_DIR = REPO_DIR / 'harness'
WORK_DIR = REPO_DIR / 'tmp'

DNS_DOMAIN = 'host.container.internal'
DNS_LOCALHOST = '203.0.113.113'

def web_search(query: str, max_results: int | None = None) -> str:
  if not query.strip():
    return 'Error: query must be a non-empty string'
  
  if max_results is None:
    max_results = 3

  if max_results < 1:
    return 'Error: max_results must be a positive integer'

  try:
    results = DDGS().text(query, max_results=max_results)
  except Exception as error:
    return f'Error: web search failed: {error}'

  return json.dumps(results, ensure_ascii=False, indent=2)

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

def stop_on_sigterm(signum: int, frame: FrameType | None) -> None:
  raise SystemExit

def stop_process(process: subprocess.Popen[Any]) -> None:
  if process.poll() is None:
    process.terminate()
    try:
      process.wait(timeout=5)
    except subprocess.TimeoutExpired:
      process.kill()
      process.wait()

def read_harness_event(process: subprocess.Popen[str]) -> dict[str, Any] | None:
  stdout = process.stdout
  if stdout is None:
    print('The harness process has no output stream.', file=sys.stderr)
    return None

  line = stdout.readline()
  if not line:
    return None

  try:
    event = json.loads(line)
  except json.JSONDecodeError:
    print(f'Unexpected harness output: {line}', file=sys.stderr, end='')
    return None

  if not isinstance(event, dict):
    print(f'Invalid harness event: {line}', file=sys.stderr, end='')
    return None

  return event

def wait_for_harness(process: subprocess.Popen[str]) -> bool:
  event = read_harness_event(process)
  if event is None:
    print('The harness process exited during startup.', file=sys.stderr)
    return False

  if event.get('type') != 'ready':
    print('The harness process did not become ready.', file=sys.stderr)
    return False

  return True

def display_harness_response(
  process: subprocess.Popen[str], stdin: TextIO
) -> bool:
  while True:
    event = read_harness_event(process)
    if event is None:
      print('The harness process exited before completing the response.',
            file=sys.stderr)
      return False

    event_type = event.get('type')

    if event_type == 'output':
      text = event.get('text', '')
      if isinstance(text, str):
        print(text, end='', flush=True)

    elif event_type == 'error':
      print(event.get('text', 'Harness error'), file=sys.stderr)

    elif event_type == 'web_search':
      arguments = event.get('arguments')

      if not isinstance(arguments, dict):
        result = 'Error: web_search arguments must be an object'
      else:
        query = arguments.get('query')
        max_results = arguments.get('max_results')
        
        if not isinstance(query, str):
          result = 'Error: query must be a string'
        elif (max_results is not None
              and (not isinstance(max_results, int)
                   or isinstance(max_results, bool))):
          result = 'Error: max_results must be a positive integer'
        else:
          result = web_search(query, max_results)

      try:
        stdin.write(
          json.dumps({'type': 'host_response', 'result': result}) + '\n'
        )
        stdin.flush()
      except OSError as error:
        print(f'Error returning host tool result: {error}', file=sys.stderr)
        return False

    elif event_type == 'done':
      return True

def run_harness() -> None:
  command = [
    'container', 'run', '-i', '--rm', '--progress', 'none',
    '--mount', f'type=bind,source={HARNESS_DIR},target=/harness,readonly',
    '--mount', f'type=bind,source={WORK_DIR},target=/work',
    '--workdir', '/work',
    'ai-harness:latest',
    'python', '/harness/harness.py'
  ]

  try:
    process = subprocess.Popen(
      command,
      stdin=subprocess.PIPE,
      stdout=subprocess.PIPE,
      text=True,
      bufsize=1
    )
  except OSError as error:
    print(f'Error starting container harness: {error}', file=sys.stderr)
    return

  atexit.register(stop_process, process)
  
  stdin = process.stdin
  if stdin is None:
    print('The harness process has no input stream.', file=sys.stderr)
    return
  
  if not wait_for_harness(process):
    stdin.close()
    process.wait()
    return

  while True:
    try:
      message = input('>>> ')
    except EOFError:
      print()
      break

    print()

    try:
      stdin.write(json.dumps({'message': message}) + '\n')
      stdin.flush()
    except OSError as error:
      print(f'Error sending prompt to harness: {error}', file=sys.stderr)
      break

    if not display_harness_response(process, stdin):
      break
    
    print()

  try:
    stdin.close()
  except OSError:
    pass

  process.wait()

def main() -> None:
  if not CONFIG_FILE.is_file():
    print('Create env/local.env from env/local.env.example', file=sys.stderr)
    return

  ssh_target = read_ssh_target(CONFIG_FILE)
  if not ssh_target:
    print('Set SSH_TARGET in env/local.env', file=sys.stderr)
    return

  if dns_is_configured():
    print('Container access to the host is already configured.')
  else:
    print('Configuring container access to host SSH tunnel...')
    try:
      subprocess.run([
        'sudo', 'container', 'system', 'dns', 'create', DNS_DOMAIN,
        '--localhost', DNS_LOCALHOST
      ], check=False)

    except OSError as error:
      print(f'Error running sudo: {error}', file=sys.stderr)

  print('Starting SSH tunnel on port 11435...')

  signal.signal(signal.SIGTERM, stop_on_sigterm)
  try:
    ssh_process = subprocess.Popen([
      'ssh', '-N', '-L', '11435:127.0.0.1:11434', ssh_target
    ],  stdin=subprocess.DEVNULL)
  except OSError as error:
    print(f'Error running ssh: {error}', file=sys.stderr)
    return
  
  atexit.register(stop_process, ssh_process)

  print()

  run_harness()

if __name__ == '__main__':
  main()
