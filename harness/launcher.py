"""SSH-side client that asks the GUI-session service to start a worker."""
import argparse
import json
from pathlib import Path
import select
import socket
import sys
from typing import Any


SOCKET_PATH = Path.home() / 'Library' / 'Caches' / 'ai-harness' / 'agent.sock'


def read_events(sock: socket.socket) -> int:
  buffer = bytearray()
  while True:
    ready, _, _ = select.select([sock, sys.stdin.buffer], [], [])
    if sys.stdin.buffer in ready:
      if not sys.stdin.buffer.read1(4096):
        # Closing the SSH command's stdin tells the LaunchAgent to stop this run.
        return 0
    if sock in ready:
      chunk = sock.recv(4096)
      if not chunk:
        raise RuntimeError('GUI LaunchAgent closed the session unexpectedly')
      buffer.extend(chunk)
      while b'\n' in buffer:
        line, _, rest = buffer.partition(b'\n')
        buffer = bytearray(rest)
        event = json.loads(line.decode('utf-8'))
        if not isinstance(event, dict):
          raise RuntimeError('Invalid response from GUI LaunchAgent')
        event_type = event.get('type')
        if event_type == 'started':
          print('GUI-session worker started.', file=sys.stderr, flush=True)
        elif event_type == 'error':
          raise RuntimeError(str(event.get('text', 'GUI LaunchAgent error')))
        elif event_type == 'done':
          exit_code = event.get('exit_code', 1)
          return exit_code if isinstance(exit_code, int) else 1


def request(action: str, project_relative: str | None = None) -> int:
  try:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
      sock.settimeout(10)
      sock.connect(str(SOCKET_PATH))
      sock.settimeout(None)
      message: dict[str, Any] = {'action': action}
      if project_relative is not None:
        message['project_relative'] = project_relative
      sock.sendall((json.dumps(message) + '\n').encode('utf-8'))
      if action == 'ping':
        response = bytearray()
        while b'\n' not in response:
          chunk = sock.recv(4096)
          if not chunk:
            raise RuntimeError('GUI LaunchAgent closed the health check')
          response.extend(chunk)
        event = json.loads(response.split(b'\n', 1)[0].decode('utf-8'))
        if isinstance(event, dict) and event.get('type') == 'ready':
          print('GUI LaunchAgent is ready.', flush=True)
          return 0
        if not isinstance(event, dict):
          raise RuntimeError('Invalid response from GUI LaunchAgent')
        raise RuntimeError(str(event.get('text', 'GUI LaunchAgent is not ready')))
      return read_events(sock)
  except (OSError, json.JSONDecodeError, RuntimeError) as error:
    print(
      f'{error}. Log into the VM desktop and run harness/bootstrap.sh to install or start the LaunchAgent.',
      file=sys.stderr,
      flush=True,
    )
    return 1


def main() -> None:
  parser = argparse.ArgumentParser(description='Send a request to the AI Harness GUI service.')
  subparsers = parser.add_subparsers(dest='action', required=True)
  subparsers.add_parser('ping', help='check whether the GUI LaunchAgent is running')
  run_parser = subparsers.add_parser('run', help='start one worker session')
  run_parser.add_argument('--project-relative', required=True)
  args = parser.parse_args()
  result = request(args.action, getattr(args, 'project_relative', None))
  raise SystemExit(result)


if __name__ == '__main__':
  main()
