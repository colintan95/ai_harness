from ssh_tunnel import start_ssh_tunnel
from utils import stop_process

from ddgs import DDGS
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings

from pathlib import Path
from types import FrameType
from typing import Any, IO
import atexit
import json
import signal
import subprocess
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
CONFIG_FILE = REPO_DIR / 'env' / 'local.env'

HARNESS_DIR = REPO_DIR / 'harness'
WORK_DIR = REPO_DIR / 'tmp'

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
  process: subprocess.Popen[str], stdin: IO[str]
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

  prompt_bindings = KeyBindings()

  # Needed to support entering multi-line iprompts
  @prompt_bindings.add('enter')
  def submit_prompt(event: Any) -> None:
    event.current_buffer.validate_and_handle()

  # 'Ctrl-c' clears the prompt if non-empty, else it ends the program
  @prompt_bindings.add('c-c')
  def clear_or_exit_prompt(event: Any) -> None:
    if event.current_buffer.text:
      event.current_buffer.reset()
    else:
      event.app.exit(exception=EOFError)

  prompt_session = PromptSession(
    key_bindings=prompt_bindings,
    multiline=True
  )
  while True:
    try:
      message = prompt_session.prompt('>>> ')
    except EOFError:
      break

    print(flush=True)

    try:
      stdin.write(json.dumps({'message': message}) + '\n')
      stdin.flush()
    except OSError as error:
      print(f'Error sending prompt to harness: {error}', file=sys.stderr)
      break

    if not display_harness_response(process, stdin):
      break

  try:
    stdin.close()
  except OSError:
    pass

  process.wait()

def stop_on_sigterm(signum: int, frame: FrameType | None) -> None:
  raise SystemExit

def main() -> None:
    # So that the atexit() cleanup handles are called on program end
  signal.signal(signal.SIGTERM, stop_on_sigterm)

  if not start_ssh_tunnel(CONFIG_FILE):
    return
  print()

  run_harness()

if __name__ == '__main__':
  main()
