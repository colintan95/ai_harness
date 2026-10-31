from typing import Any
import subprocess

def stop_process(process: subprocess.Popen[Any]) -> None:
  if process.poll() is None:
    process.terminate()
    try:
      process.wait(timeout=5)
    except subprocess.TimeoutExpired:
      process.kill()
      process.wait()
