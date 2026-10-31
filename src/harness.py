import requests
import json

URL= 'http://host.container.internal:11435/api/chat'
MODEL = 'qwen3.8:27b'

messages = []

while True:
  try:
    message = input('>>> ')
  except EOFError:
    print()
    break

  print()

  messages.append({'role': 'user', 'content': message})
  payload = {
    'model': MODEL,
    'messages': messages,
    'stream': True
  }

  with requests.post(URL, json=payload, stream=True, timeout=30) as response:
    response.raise_for_status()

    for line in response.iter_lines():
      if not line:
        continue

      chunk = json.loads(line)
      text = chunk.get('message', {}).get('content', '')
      print(text, end='', flush=True)

  print()
  print()