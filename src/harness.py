from typing import Any
import requests
import json

def hello_tool() -> str:
  return 'Hello, Tool!'

URL= 'http://host.container.internal:11435/api/chat'
MODEL = 'qwen3.8:27b'

TOOLS = {
  'hello_tool': hello_tool
}
TOOLS_SCHEMA = [
  {
    'type': 'function',
    'function': {
      'name': 'hello_tool',
      'description': 'Uses the Hello, Tool! tool',
      'parameters': {}
    }
  }
]

def send_prompt(messages: list[dict[str, Any]]) -> None:
  while True:
    payload: dict[str, Any] = {
      'model': MODEL,
      'messages': messages,
      'tools': TOOLS_SCHEMA,
      'stream': True
    }

    content = ''
    tool_calls = []

    with requests.post(URL, json=payload, stream=True, timeout=30) as response:
      response.raise_for_status()

      for line in response.iter_lines():
        if not line:
          continue

        chunk = json.loads(line)
        message = chunk.get('message', {})

        text = message.get('content', '')
        content += text
        print(text, end='', flush=True)

        tool_calls.extend(message.get('tool_calls', []))

    # If there was a reply, add a newline at the end of it
    if content:
      print()

    agent_msg: dict[str, Any] = {
      'role': 'assistant',
      'content': content
    }
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

if __name__ == '__main__':
  messages = []
  while True:
    try:
      message = input('>>> ')
    except EOFError:
      print()
      break

    print()

    messages.append({'role': 'user', 'content': message})
    send_prompt(messages)
    
    print()
