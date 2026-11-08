"""Configure the UTM VM to share the harness code and ~/work."""
import argparse
from pathlib import Path
import subprocess
import sys


HARNESS_DIR = Path(__file__).resolve().parent.parent / 'harness'
WORK_DIR = Path.home() / 'work'

SET_SHARE = '''on run argv
  local harnessVMRef, harnessVMMatches, harnessShares
  tell application "UTM"
    set harnessVMMatches to virtual machines whose id is (item 1 of argv)
    if (count of harnessVMMatches) is 0 then
      set harnessVMMatches to virtual machines whose name is (item 1 of argv)
    end if
    if (count of harnessVMMatches) is not 1 then error "VM name or UUID must identify one VM"
    set harnessVMRef to item 1 of harnessVMMatches
    if backend of harnessVMRef is not apple then error "A macOS Apple backend VM is required"
    if status of harnessVMRef is started then error "Shut down the VM before configuring its shared directories"
    update registry harnessVMRef with {POSIX file (item 2 of argv), POSIX file (item 3 of argv)}
    set harnessShares to registry of harnessVMRef
    if (count of harnessShares) is not 2 then error "Expected exactly two shared directories after update"
    return (POSIX path of (item 1 of harnessShares)) & linefeed & (POSIX path of (item 2 of harnessShares))
  end tell
end run'''


def configure_share(vm_id: str) -> Path:
  harness_dir = HARNESS_DIR.resolve(strict=True)
  if not harness_dir.is_dir():
    raise RuntimeError(f'Harness directory is not a directory: {harness_dir}')
  WORK_DIR.mkdir(parents=True, exist_ok=True)
  work_dir = WORK_DIR.resolve(strict=True)
  if not work_dir.is_dir():
    raise RuntimeError(f'Work directory is not a directory: {work_dir}')
  if harness_dir == work_dir:
    raise RuntimeError('The harness and work directories must be different')

  try:
    result = subprocess.run(
      ['osascript', '-e', SET_SHARE, vm_id, str(harness_dir), str(work_dir)],
      capture_output=True, text=True, check=True, timeout=30,
    )
  except subprocess.CalledProcessError as error:
    details = error.stderr.strip() or error.stdout.strip()
    raise RuntimeError(f'UTM could not configure the shared directory: {details}') from error
  except subprocess.TimeoutExpired as error:
    raise RuntimeError('Timed out while configuring the UTM shared directory') from error
  except FileNotFoundError as error:
    raise RuntimeError('osascript was not found; this command must run on macOS') from error

  configured = {Path(path).resolve() for path in result.stdout.splitlines() if path}
  expected = {harness_dir, work_dir}
  if configured != expected:
    raise RuntimeError(
      f'UTM reports {sorted(map(str, configured))} as shared directories; '
      f'expected {sorted(map(str, expected))}'
    )
  return work_dir


def main() -> int:
  parser = argparse.ArgumentParser(
    description='Configure a UTM macOS VM to share this repository’s harness folder and ~/work.'
  )
  parser.add_argument('vm_id', help='UTM VM name or UUID')
  args = parser.parse_args()

  try:
    work_dir = configure_share(args.vm_id)
  except (OSError, RuntimeError) as error:
    print(f'Error: {error}', file=sys.stderr)
    return 1

  print(f'UTM VM {args.vm_id!r} will share {HARNESS_DIR.resolve()} and {work_dir}')
  print('Start the VM to mount it in the guest.')
  return 0


if __name__ == '__main__':
  raise SystemExit(main())
