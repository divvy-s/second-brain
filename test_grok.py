import os, urllib.request, json
from dotenv import load_dotenv

load_dotenv()
api_key = os.environ.get('XAI_API_KEY')

for model in ['grok-beta', 'grok-2-latest', 'grok-3']:
    print(f'Testing {model}...')
    req = urllib.request.Request(
        'https://api.x.ai/v1/chat/completions', 
        method='POST', 
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}, 
        data=json.dumps({'model': model, 'messages': [{'role': 'user', 'content': 'hi'}]}).encode('utf-8')
    )
    try:
        res = urllib.request.urlopen(req)
        print('SUCCESS:', json.loads(res.read().decode('utf-8'))['choices'][0]['message']['content'])
    except Exception as e:
        error_body = e.read().decode('utf-8') if hasattr(e, 'read') else str(e)
        print('ERROR:', e, error_body)
