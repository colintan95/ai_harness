from ddgs import DDGS
import requests

from pathlib import Path
from typing import Any, IO, TypedDict
import argparse
import base64
import json
import socket
import subprocess

WORK_DIR = Path('/work').resolve()

HOSTNAME = 'host.container.internal'
OLLAMA_URL= f'http://{HOSTNAME}:11435/api/chat'

MODEL = 'qwen3.8:27b'

SYSTEM_PROMPT = (WORK_DIR / 'AGENTS.md').read_text(encoding='utf-8')

def run_command(command: str) -> str:
  if not command.strip():
    return 'Error: command cannot be empty'

  # TODO: Figure out what the working dir should be, or how to get and set it
  try:
    result = subprocess.run(
      command,
      cwd=WORK_DIR,
      shell=True,
      capture_output=True,
      text=True,
      timeout=30,
      check=False
    )
  except subprocess.TimeoutExpired:
    return 'Error: command timed out after 30 seconds'
  except OSError as error:
    return f'Error running command: {error}'

  return (
    f'exit_code: {result.returncode}\n'
    f'stdout:\n{result.stdout}\n'
    f'stderr:\n{result.stderr}'
  )

def web_search(query: str, max_results: int | None = None) -> str:
  if not isinstance(query, str) or not query.strip():
    return 'Error: query must be a non-empty string'

  if max_results is None:
    max_results = 3
  elif (not isinstance(max_results, int)
        or isinstance(max_results, bool)
        or max_results < 1):
    return 'Error: max_results must be a positive integer'

  try:
    results = DDGS().text(query, max_results=max_results)
  except Exception as error:
    return f'Error: web search failed: {error}'

  return json.dumps(results, ensure_ascii=False, indent=2)

class ImageToolResult(TypedDict):
  content: str
  images: list[str]

def view_image(path: str) -> ImageToolResult:
  if not isinstance(path, str) or not path.strip():
    return {'content': 'Error: provide an image path inside /work', 
            'images': []}

  image_path = (WORK_DIR / path).resolve()
  try:
    image_path.relative_to(WORK_DIR)
  except ValueError:
    return {'content': 'Error: image must be inside /work', 'images': []}

  if not image_path.is_file():
    return {'content': f'Error: image not found: {path}', 'images': []}

  IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp'}
  if image_path.suffix.lower() not in IMAGE_SUFFIXES:
    return {
      'content': 'Error: use a PNG, JPEG, or WebP image',
      'images': []
    }

  MAX_IMAGE_BYTES = 12 * 1024 * 1024
  try:
    if image_path.stat().st_size > MAX_IMAGE_BYTES:
      return {'content': 'Error: image is too large', 'images': []}
    image = base64.b64encode(image_path.read_bytes()).decode('ascii')

  except OSError as error:
    return {'content': f'Error reading image: {error}', 'images': []}

  return {
    'content': f'Attached image: {image_path.name}',
    'images': [image]
  }

TOOLS_SCHEMA = [
  {
    'type': 'function',
    'function': {
      'name': 'run_command',
      'description': 'Executes a CLI command string in /work using the shell.',
      'parameters': {
        'type': 'object',
        'properties': {
          'command': {
            'type': 'string',
            'description': 'CLI command to execute, e.g. "git status" or "ls -la"'
          }
        },
        'required': ['command']
      }
    }
  },
  {
    'type': 'function',
    'function': {
      'name': 'web_search',
      'description': 'Search the web with DDGS for current information.',
      'parameters': {
        'type': 'object',
        'properties': {
          'query': {
            'type': 'string',
            'description': 'The web search query'
          },
          'max_results': {
            'type': 'integer',
            'description': 'Maximum number of results to return',
            'default': 3
          }
        },
        'required': ['query']
      }
    }
  },
  {
    'type': 'function',
    'function': {
      'name': 'view_image',
      'description': 'Attaches an image from /work so you can inspect it.',
      'parameters': {
        'type': 'object',
        'properties': {
          'path': {
            'type': 'string',
            'description': 'Image path inside /work, e.g. screenshots/error.png'
          }
        },
        'required': ['path']
      }
    }
  }
]

def send_event(writer: IO[str], event: dict[str, Any]) -> None:
  writer.write(json.dumps(event) + '\n')
  writer.flush()

def send_output(writer: IO[str], text: str) -> None:
  send_event(writer, {'type': 'output', 'text': text})

def handle_prompt(messages: list[dict[str, Any]], writer: IO[str]) -> None:
  while True:
    payload: dict[str, Any] = {
      'model': MODEL,
      'messages': messages,
      'tools': TOOLS_SCHEMA,
      'think': True,
      'stream': True
    }

    current_token_count = None

    thinking = ''
    reply = ''
    mode: str | None = None
    tool_calls = []

    with requests.post(OLLAMA_URL, json=payload, stream=True) as response:
      response.raise_for_status()

      for line in response.iter_lines():
        if not line:
          continue

        chunk = json.loads(line)

        # 'prompt_eval_count' is the input token count while 'eval_count' is the
        # output token count
        prompt_eval_count = chunk.get('prompt_eval_count', 0)
        eval_count = chunk.get('eval_count', 0)
        if (isinstance(prompt_eval_count, int)
            and not isinstance(prompt_eval_count, bool)
            and isinstance(eval_count, int)
            and not isinstance(eval_count, bool)):
          current_token_count = prompt_eval_count + eval_count

        message = chunk.get('message', {})

        # We assume here that the model won't interleave thinking and reply
        # chunks - and that whenever the chunk changes to the other type we
        # can change the mode

        thought = message.get('thinking', '')
        thinking += thought

        content = message.get('content', '')
        reply += content

        # A chunk with no thought or content shouldn't affect the mode
        if thought or content:
          if thought:
            new_mode = 'thinking'
          else:
            new_mode = 'reply'

          # Create a line gap when switching between thinking and replies
          if mode and mode != new_mode:
            send_output(writer, '\n\n')

          mode = new_mode

        if thought:
          DARK_GRAY = '\033[38;5;240m'
          RESET = '\033[0m'
          send_output(writer, f'{DARK_GRAY}{thought}{RESET}')
        
        if content:
          send_output(writer, content)

        tool_calls.extend(message.get('tool_calls', []))

    # Add a line gap after the last thinking or reply from the request
    if thinking or reply:
      send_output(writer, '\n\n')

    agent_msg: dict[str, Any] = {
      'role': 'assistant',
      'content': reply
    }

    # TODO: See if there are cases that the model degenerates cos we discard
    # its chain of thought, and see how we can deal with that without having
    # to waste its context on all its thoughts. E.g. if the model calls a tool
    # within its chain of thought - since we would discard all its thoughts
    # before the tool call

    # TODO: Experiment around with whether we should include thinking or not
    # in the context window
    if thinking:
      agent_msg['thinking'] = thinking

    # TODO: See if there's stale data that can be discarded from the context -
    # e.g. if the agent rereads a file
    if tool_calls:
      agent_msg['tool_calls'] = tool_calls

    # TODO: Check if we're ever adding any blank messages from the agent -
    # e.g. if the request had no reply or it was all thought
    messages.append(agent_msg)

    # Stop the loop once there aren't any tool calls left
    if not tool_calls:
      if current_token_count is not None:
        send_output(writer, f'Current token count: {current_token_count}\n\n')
      break

    for call in tool_calls:
      fn = call['function']
      name, args = fn['name'], fn['arguments']

      send_output(
        writer,
        f'Calling tool: {name}({json.dumps(args, ensure_ascii=False)})\n\n'
      )

      content = ''
      images = []

      if name == 'run_command':
        content = run_command(**args)
      elif name == 'web_search':
        content = web_search(**args)
      elif name == 'view_image':
        result = view_image(**args)
        content = result['content']
        if result['images']:
          images = result['images']
      else:
        content = f'Unknown tool {name}'

      tool_msg: dict[str, Any] = {
        'role': 'tool',
        'tool_name': name,
        'content': content
      }
      if images:
        tool_msg['images'] = images

      messages.append(tool_msg)

def run_worker(host_port: int) -> None:
  messages = []
  if SYSTEM_PROMPT:
    messages.append({'role': 'system', 'content': SYSTEM_PROMPT})

  with socket.create_connection((HOSTNAME, host_port), timeout=30) as conn:
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    conn.settimeout(None)

    reader = conn.makefile('r', encoding='utf-8')
    writer = conn.makefile('w', encoding='utf-8')

    with reader, writer:
      send_event(writer, {'type': 'ready'})

      # The loop ends when the host closes the connection
      for line in reader:
        try:
          request = json.loads(line)
          prompt = request.get('prompt')

          if not isinstance(prompt, str):
            raise ValueError('Request must contain a string message')

          messages.append({'role': 'user', 'content': prompt})
          handle_prompt(messages, writer)

        except Exception as error:
          send_event(writer, {'type': 'error', 'text': str(error)})

        send_event(writer, {'type': 'done'})

if __name__ == '__main__':
  parser = argparse.ArgumentParser()
  parser.add_argument('--host-port', type=int, required=True)
  
  run_worker(parser.parse_args().host_port)
