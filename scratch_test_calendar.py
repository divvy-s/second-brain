import json
import os
import subprocess

from dotenv import load_dotenv

load_dotenv()

payload={'type': 'create_calendar_event', 'title': 'Test Meeting', 'start_time': '2026-05-05T16:00:00+05:30', 'end_time': None}
res = subprocess.run(
    ['python', 'scripts/external_cli.py', 'calendar', 'execute_action'], 
    input=json.dumps({'config': {'access_token': os.environ.get('CALENDAR_ACCESS_TOKEN')}, 'payload': payload}), 
    text=True, capture_output=True
)
print("STDOUT:", res.stdout.strip())
print("STDERR:", res.stderr.strip())
