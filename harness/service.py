"""LaunchAgent supervisor that starts desktop-capable workers in the GUI session."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
from typing import Any

from permissions import request_permissions


SOCKET_PATH = Path.home() / 'Library' / 'Caches' / 'ai-harness' / 'agent.sock'
MAX_REQUEST_BYTES = 64 * 1024


def send_event(conn: socket.socket, event: dict[str, Any]) -> None:
  conn.sendall((json.dumps(event) + '\n').encode('utf-8'))


def receive_request(conn: socket.socket) -> dict[str, Any]:
  data = bytearray()
  while len(data) <= MAX_REQUEST_BYTES:
    chunk = conn.recv(4096)
    if not chunk:
      raise ValueError('Client closed before sending a request')
    newline = chunk.find(b'\n')
    if newline >= 0:
      data.extend(chunk[:newline])
      if len(data) > MAX_REQUEST_BYTES:
        raise ValueError('Request is too large')
      request = json.loads(data.decode('utf-8'))
      if not isinstance(request, dict):
        raise ValueError('Request must be a JSON object')
      return request
    data.extend(chunk)
  raise ValueError('Request is too large')


def stop_worker(process: subprocess.Popen[bytes]) -> None:
  if process.stdin is not None and not process.stdin.closed:
    try:
      process.stdin.close()
    except OSError:
      pass
  try:
    process.wait(timeout=15)
  except subprocess.TimeoutExpired:
    try:
      os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
      pass
    try:
      process.wait(timeout=5)
    except subprocess.TimeoutExpired:
      try:
        os.killpg(process.pid, signal.SIGKILL)
      except ProcessLookupError:
        pass
      process.wait()


class Service:
  def __init__(self, install_dir: Path, work_dir: Path) -> None:
    self.install_dir = install_dir.expanduser().resolve()
    self.work_dir = work_dir.expanduser().resolve()
    self.running = True
    self.server: socket.socket | None = None
    self.active_worker: subprocess.Popen[bytes] | None = None
    self.worker_lock = threading.Lock()

  def stop(self, signum: int, frame: object) -> None:
    del signum, frame
    self.running = False
    if self.server is not None:
      self.server.close()
    with self.worker_lock:
      active_worker = self.active_worker
    if active_worker is not None and active_worker.poll() is None:
      stop_worker(active_worker)

  def start_worker(self, conn: socket.socket, project_relative: str) -> None:
    project_path = Path(project_relative)
    if project_path.is_absolute() or '..' in project_path.parts:
      send_event(conn, {'type': 'error', 'text': 'Project path must stay inside the shared workspace'})
      return

    harness_script = self.install_dir / 'harness.py'
    if not harness_script.is_file():
      send_event(conn, {'type': 'error', 'text': f'Missing harness script: {harness_script}'})
      return

    with self.worker_lock:
      worker_is_running = (
        self.active_worker is not None and self.active_worker.poll() is None
      )
    if worker_is_running:
      send_event(conn, {'type': 'error', 'text': 'A worker is already running'})
      return

    log_dir = Path.home() / 'Library' / 'Logs' / 'AIHarness'
    log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    log_path = log_dir / 'harness.log'
    env = os.environ.copy()
    env['PATH'] = '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'
    command = [
      sys.executable, '-u', str(harness_script),
      '--work-dir', str(self.work_dir),
      '--project-relative', project_path.as_posix(),
      '--host-port', '9000',
    ]

    try:
      with log_path.open('ab', buffering=0) as log:
        process = subprocess.Popen(
          command,
          stdin=subprocess.PIPE,
          stdout=log,
          stderr=subprocess.STDOUT,
          env=env,
          start_new_session=True,
        )
        with self.worker_lock:
          self.active_worker = process

        threading.Thread(
          target=self.wait_for_worker,
          args=(process,),
          name='harness-worker-monitor',
          daemon=True,
        ).start()
        send_event(conn, {'type': 'started', 'pid': process.pid})
    except OSError as error:
      try:
        send_event(conn, {'type': 'error', 'text': f'Could not start worker: {error}'})
      except OSError:
        pass

  def wait_for_worker(self, process: subprocess.Popen[bytes]) -> None:
    process.wait()
    if process.stdin is not None and not process.stdin.closed:
      try:
        process.stdin.close()
      except OSError:
        pass
    with self.worker_lock:
      if self.active_worker is process:
        self.active_worker = None

  def handle(self, conn: socket.socket) -> None:
    try:
      request = receive_request(conn)
      action = request.get('action')
      if action == 'ping':
        send_event(conn, {'type': 'ready'})
      elif action == 'run':
        project_relative = request.get('project_relative')
        if not isinstance(project_relative, str) or not project_relative:
          send_event(conn, {'type': 'error', 'text': 'project_relative must be a non-empty string'})
          return
        self.start_worker(conn, project_relative)
      else:
        send_event(conn, {'type': 'error', 'text': 'Unknown service action'})
    except (UnicodeDecodeError, json.JSONDecodeError, OSError, ValueError) as error:
      try:
        send_event(conn, {'type': 'error', 'text': str(error)})
      except OSError:
        pass

  def serve(self) -> None:
    threading.Thread(
      target=request_permissions,
      name='macos-permission-requests',
      daemon=True,
    ).start()
    SOCKET_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if SOCKET_PATH.exists():
      if not SOCKET_PATH.is_socket():
        raise RuntimeError(f'Refusing to replace non-socket path: {SOCKET_PATH}')
      try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as existing:
          existing.settimeout(1)
          existing.connect(str(SOCKET_PATH))
        raise RuntimeError('Another AI Harness LaunchAgent is already listening')
      except (ConnectionRefusedError, FileNotFoundError, TimeoutError):
        SOCKET_PATH.unlink()

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    self.server = server
    server.bind(str(SOCKET_PATH))
    SOCKET_PATH.chmod(0o600)
    server.listen(2)
    server.settimeout(1)
    print(f'AI Harness GUI service listening on {SOCKET_PATH}', flush=True)

    signal.signal(signal.SIGTERM, self.stop)
    signal.signal(signal.SIGINT, self.stop)
    try:
      while self.running:
        try:
          conn, _ = server.accept()
        except socket.timeout:
          continue
        except OSError:
          if self.running:
            raise
          break
        with conn:
          self.handle(conn)
    finally:
      server.close()
      try:
        SOCKET_PATH.unlink()
      except FileNotFoundError:
        pass


def main() -> None:
  parser = argparse.ArgumentParser(description='Run the AI Harness GUI LaunchAgent service.')
  parser.add_argument('--install-dir', type=Path, required=True)
  parser.add_argument('--work-dir', type=Path, required=True)
  args = parser.parse_args()
  Service(args.install_dir, args.work_dir).serve()


if __name__ == '__main__':
  main()
