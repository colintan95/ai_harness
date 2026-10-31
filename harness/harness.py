from pathlib import Path
from typing import Any
import json
import requests
import subprocess
import sys

WORK_DIR = Path('/work').resolve()

def run_command(command: str) -> str:
  if not command.strip():
    return 'Error: command cannot be empty'

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

URL= 'http://host.container.internal:11435/api/chat'
MODEL = 'qwen3.8:27b'

def web_search(query: str, max_results: int | None = None) -> str:
  arguments: dict[str, Any] = {'query': query}
  if max_results is not None:
    arguments['max_results'] = max_results

  print(json.dumps({
    'type': 'web_search',
    'arguments': arguments
  }), flush=True)

  line = sys.stdin.readline()
  if not line:
    return 'Error: host closed the input before returning web search results'

  try:
    response = json.loads(line)
  except json.JSONDecodeError:
    return 'Error: received an invalid response from the host'

  if not isinstance(response, dict) or response.get('type') != 'host_response':
    return 'Error: received an unexpected response from the host'

  result = response.get('result')
  if not isinstance(result, str):
    return 'Error: host returned an invalid web search result'

  return result

TOOLS = {
  'run_command': run_command,
  'web_search': web_search
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
  }
]

def send_prompt(messages: list[dict[str, Any]]) -> None:
  while True:
    payload: dict[str, Any] = {
      'model': MODEL,
      'messages': messages,
      'tools': TOOLS_SCHEMA,
      'think': True,
      'stream': True
    }

    thinking = ''
    is_thinking = False
    
    reply = ''
    is_reply = False
    
    tool_calls = []

    with requests.post(URL, json=payload, stream=True) as response:
      response.raise_for_status()

      for line in response.iter_lines():
        if not line:
          continue

        chunk = json.loads(line)
        message = chunk.get('message', {})

        thought = message.get('thinking', '')
        thinking += thought

        if thought:
          if is_reply:
            emit_output('\n\n')
            is_reply = False

          is_thinking = True

          DARK_GRAY = '\033[38;5;240m'
          RESET = '\033[0m'
          emit_output(f'{DARK_GRAY}{thought}{RESET}')
        
        content = message.get('content', '')
        reply += content

        # Apply line breaks to split the thoughts and the actual reply
        if content and is_thinking:
          is_thinking = False
          emit_output('\n\n')

        if content:
          is_reply = True
          emit_output(content)

        tool_calls.extend(message.get('tool_calls', []))

    # If there was a reply, add a newline at the end of it
    if reply:
      emit_output('\n')

    agent_msg: dict[str, Any] = {
      'role': 'assistant',
      'content': reply
    }

    if thinking:
      agent_msg['thinking'] = thinking
      
    if tool_calls:
      agent_msg['tool_calls'] = tool_calls

    # Save the message from the agent
    messages.append(agent_msg)

    # Stop the loop once there aren't any tool calls left
    if not tool_calls:
      break

    for call in tool_calls:
      fn = call['function']
      name, args = fn['name'], fn['arguments']

      tool = TOOLS.get(name)
      if tool is None:
        result = f'Unknown tool {name}'
      else:
        result = tool(**args)

      messages.append({
        'role': 'tool',
        'tool_name': name,
        'content': str(result)
      })

def emit_output(text: str) -> None:
  print(json.dumps({'type': 'output', 'text': text}), flush=True)

SYSTEM_PROMPT = """For python
- Add type information
- Use two space indentation"""

def run_worker() -> None:
  messages = []
  if SYSTEM_PROMPT:
    messages.append({'role': 'system', 'content': SYSTEM_PROMPT})

  print(json.dumps({'type': 'ready'}), flush=True)
  for line in sys.stdin:
    try:
      request = json.loads(line)
      message = request.get('message')
      
      if not isinstance(message, str):
        raise ValueError('Request must contain a string message')

      messages.append({'role': 'user', 'content': message})
      send_prompt(messages)

    except Exception as error:
      print(json.dumps({'type': 'error', 'text': str(error)}), flush=True)

    print(json.dumps({'type': 'done'}), flush=True)

if __name__ == '__main__':
  run_worker()
