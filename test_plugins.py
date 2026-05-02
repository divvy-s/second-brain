import json, subprocess, os
from dotenv import load_dotenv

load_dotenv()

def test_plugin(plugin, payload, config_key, config_val):
    print(f"\n--- Testing {plugin} ---")
    res = subprocess.run(
        ['python', 'scripts/external_cli.py', plugin, 'execute_action'],
        input=json.dumps({'config': {config_key: config_val}, 'payload': payload}),
        text=True, capture_output=True
    )
    if res.stdout.strip():
        print("STDOUT:", res.stdout.strip())
    if res.stderr.strip():
        print("STDERR:", res.stderr.strip())

# Test Telegram
test_plugin('telegram', {'type': 'send_message', 'recipient': 'naman', 'text': 'test'}, 'bot_token', os.environ.get('TELEGRAM_BOT_TOKEN'))

# Test Gmail
test_plugin('gmail', {'type': 'send_email', 'to': 'krishnaguptakp21@gmail.com', 'subject': 'Test', 'body': 'test'}, 'access_token', os.environ.get('GMAIL_ACCESS_TOKEN'))

# Test Calendar
test_plugin('calendar', {'type': 'create_calendar_event', 'title': 'Test', 'start_time': ''}, 'access_token', os.environ.get('CALENDAR_ACCESS_TOKEN'))
