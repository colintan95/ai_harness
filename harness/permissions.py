"""Request the macOS permissions used by the GUI worker."""
import ctypes
from ctypes import c_bool, c_long, c_void_p
import time
from typing import Callable


PROMPT_TIMEOUT_SECONDS = 90


def request_screen_recording() -> Callable[[], bool] | None:
  try:
    core_graphics = ctypes.CDLL(
      '/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics'
    )
    preflight = core_graphics.CGPreflightScreenCaptureAccess
    preflight.argtypes = []
    preflight.restype = c_bool
    if preflight():
      print('Screen Recording access is already enabled.', flush=True)
      return None

    print('Requesting Screen Recording access...', flush=True)
    request = core_graphics.CGRequestScreenCaptureAccess
    request.argtypes = []
    request.restype = c_bool
    if request():
      print('Screen Recording access is enabled.', flush=True)
      return None
    print('Approve the Screen Recording request in macOS.', flush=True)
    return lambda: bool(preflight())
  except (AttributeError, OSError) as error:
    print(f'Could not request Screen Recording access: {error}', flush=True)
    return None


def request_post_events() -> Callable[[], bool] | None:
  try:
    core_graphics = ctypes.CDLL(
      '/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics'
    )
    preflight = core_graphics.CGPreflightPostEventAccess
    preflight.argtypes = []
    preflight.restype = c_bool
    if preflight():
      print('Mouse and keyboard event access is already enabled.', flush=True)
      return None

    print('Requesting mouse and keyboard event access...', flush=True)
    request = core_graphics.CGRequestPostEventAccess
    request.argtypes = []
    request.restype = c_bool
    if request():
      print('Mouse and keyboard event access is enabled.', flush=True)
      return None
    print('Approve the mouse and keyboard event request in macOS.', flush=True)
    return lambda: bool(preflight())
  except (AttributeError, OSError) as error:
    print(f'Could not request mouse and keyboard event access: {error}', flush=True)
    return None


def request_accessibility() -> Callable[[], bool] | None:
  try:
    services = ctypes.CDLL(
      '/System/Library/Frameworks/ApplicationServices.framework/'
      'Frameworks/HIServices.framework/HIServices'
    )
    core_foundation = ctypes.CDLL(
      '/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation'
    )
    is_trusted = services.AXIsProcessTrustedWithOptions
    is_trusted.argtypes = [c_void_p]
    is_trusted.restype = c_bool
    if is_trusted(None):
      print('Accessibility access is already enabled.', flush=True)
      return None

    prompt_key = c_void_p.in_dll(
      services, 'kAXTrustedCheckOptionPrompt'
    ).value
    true_value = c_void_p.in_dll(core_foundation, 'kCFBooleanTrue').value
    if prompt_key is None or true_value is None:
      raise RuntimeError('Could not load Accessibility request constants')
    keys = (c_void_p * 1)(prompt_key)
    values = (c_void_p * 1)(true_value)

    create_dictionary = core_foundation.CFDictionaryCreate
    create_dictionary.argtypes = [
      c_void_p, ctypes.POINTER(c_void_p), ctypes.POINTER(c_void_p),
      c_long, c_void_p, c_void_p,
    ]
    create_dictionary.restype = c_void_p
    options = create_dictionary(None, keys, values, 1, None, None)
    if not options:
      raise RuntimeError('Could not create Accessibility request options')

    try:
      print('Requesting Accessibility access...', flush=True)
      if is_trusted(options):
        print('Accessibility access is enabled.', flush=True)
        return None
      print('Approve the Accessibility request in macOS.', flush=True)
    finally:
      release = core_foundation.CFRelease
      release.argtypes = [c_void_p]
      release(options)
    return lambda: bool(is_trusted(None))
  except (AttributeError, OSError, RuntimeError, ValueError) as error:
    print(f'Could not request Accessibility access: {error}', flush=True)
    return None


def wait_for_permission(
  name: str, is_enabled: Callable[[], bool],
) -> None:
  deadline = time.monotonic() + PROMPT_TIMEOUT_SECONDS
  while time.monotonic() < deadline:
    try:
      if is_enabled():
        print(f'{name} access is enabled.', flush=True)
        return
    except (OSError, RuntimeError) as error:
      print(f'Could not check {name} access: {error}', flush=True)
      return
    time.sleep(1)
  print(
    f'{name} access was not confirmed; continuing to the next request.',
    flush=True,
  )


def request_permissions() -> None:
  requests = (
    ('Accessibility', request_accessibility),
    ('Mouse and keyboard event', request_post_events),
    ('Screen Recording', request_screen_recording),
  )
  for name, request in requests:
    is_enabled = request()
    if is_enabled is not None:
      wait_for_permission(name, is_enabled)
