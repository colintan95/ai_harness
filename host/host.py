from connection import configure_connections

from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings

from pathlib import Path
from types import FrameType
from typing import Any, IO
import argparse
import json
import signal
import socket
import subprocess
import sys
import time

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
CONFIG_FILE = REPO_DIR / 'env' / 'local.env'

HARNESS_DIR = REPO_DIR / 'harness'

def ensure_container_system_running() -> bool:
  try:
    status = subprocess.run(
      ['container', 'system', 'status'],
      stdout=subprocess.DEVNULL,
      stderr=subprocess.DEVNULL,
      check=False
    )
  except OSError as error:
    print(f'Error checking container system status: {error}', file=sys.stderr)
    return False

  if status.returncode == 0:
    return True

  print('Container system is not running; starting it...', flush=True)
  try:
    subprocess.run(['container', 'system', 'start'], check=True)
  except (OSError, subprocess.CalledProcessError) as error:
    print(f'Error starting container system: {error}', file=sys.stderr)
    return False

  return True

def read_event(reader: IO[str]) -> dict[str, Any] | None:
  # Blocks until the harness sends an event back
  line = reader.readline()
  if not line:
    return None

  try:
    event = json.loads(line)
  except json.JSONDecodeError:
    print(f'Unexpected harness message: {line}', file=sys.stderr, end='')
    return None

  if not isinstance(event, dict):
    print(f'Invalid harness event: {line}', file=sys.stderr, end='')
    return None

  return event

def wait_for_harness_ready(reader: IO[str]) -> bool:
  event = read_event(reader)
  if event is None:
    print('The harness connection closed during startup.', file=sys.stderr)
    return False

  if event.get('type') != 'ready':
    print('The harness process did not become ready.', file=sys.stderr)
    return False

  return True

def handle_responses(reader: IO[str]) -> bool:
  while True:
    event = read_event(reader)
    if event is None:
      print('The harness connection closed before completing the response.',
            file=sys.stderr)
      return False

    event_type = event.get('type')

    if event_type == 'output':
      text = event.get('text', '')
      if isinstance(text, str):
        print(text, end='', flush=True)

    elif event_type == 'error':
      print(event.get('text', 'Harness error'), file=sys.stderr)

    elif event_type == 'done':
      # Ring the terminal bell once the model has completed
      print('\a', end='', flush=True)
      return True

def run_harness(work_dir: Path, backend: str = 'container') -> None:
  listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

  try:
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
  except OSError as error:
    listener.close()
    print(f'Error starting host-harness socket: {error}', file=sys.stderr)
    return
  
  port = listener.getsockname()[1]
  listener.settimeout(1)
  try:
    with configure_connections(CONFIG_FILE, work_dir, HARNESS_DIR, port, backend) as process:
      run_session(listener, process)
  except (OSError, RuntimeError, subprocess.SubprocessError) as error:
    print(f'Error running harness: {error}', file=sys.stderr)
  finally:
    listener.close()


def run_session(
  listener: socket.socket, process: subprocess.Popen[bytes]
) -> None:
  # Wait for the harness to connect

  conn: socket.socket | None = None
  deadline = time.monotonic() + 30
  try:
    while conn is None:
      try:
        conn, _ = listener.accept()
      except socket.timeout:
        if process.poll() is not None:
          print('The harness exited before connecting to the host.',
                file=sys.stderr)
          return
        if time.monotonic() >= deadline:
          print('Timed out waiting for the harness to connect.',
                file=sys.stderr)
          return
  finally:
    listener.close()

  if conn is None:
    return

  with conn:
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    reader = conn.makefile('r', encoding='utf-8')
    writer = conn.makefile('w', encoding='utf-8')

    with reader, writer:
      conn.settimeout(10)
      if not wait_for_harness_ready(reader):
        return
      conn.settimeout(None)

      prompt_bindings = KeyBindings()

      # Needed to support entering multi-line prompts
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

      prompt_session: PromptSession[str] = PromptSession(
        key_bindings=prompt_bindings,
        multiline=True
      )

      # The main prompt loop - once we break out of the loop we close the
      # connection which causes the harness to terminate
      while True:
        try:
          prompt = prompt_session.prompt('>>> ')
        except EOFError:
          print(flush=True)
          break

        print(flush=True)

        try:
          writer.write(json.dumps({'prompt': prompt}) + '\n')
          writer.flush()
        except OSError as error:
          print(f'Error sending prompt to harness: {error}', file=sys.stderr)
          break

        # Handle the responses from the harness until it says it's done
        if not handle_responses(reader):
          break

  # Wait until the harness has shut down
  process.wait(timeout=10)

def stop_on_sigterm(signum: int, frame: FrameType | None) -> None:
  raise SystemExit

def main() -> None:
  parser = argparse.ArgumentParser(
    description='Run the harness in a container or a macOS UTM guest.'
  )
  parser.add_argument(
    'work_dir',
    type=Path,
    help='host workspace folder to share with the worker'
  )
  parser.add_argument('--backend', choices=['container', 'utm'], default='container')
  args = parser.parse_args()
  work_dir = args.work_dir.expanduser().resolve()
  if not work_dir.is_dir():
    parser.error(f'work folder does not exist or is not a directory: {work_dir}')

  # Unwind the connection context managers when SIGTERM arrives.
  signal.signal(signal.SIGTERM, stop_on_sigterm)

  if args.backend == 'container' and not ensure_container_system_running():
    return

  run_harness(work_dir, args.backend)

if __name__ == '__main__':
  main()
