import sqlite3, json, sys
sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect('data/second_brain.sqlite3')
cur = conn.cursor()
cur.execute("SELECT * FROM settings_store WHERE key = 'approval_requests'")
res = cur.fetchone()
data = json.loads(res[1]) # assuming index 1 is the value
cals = [req for req in data.values() if req.get('action', {}).get('plugin') == 'calendar']
cals.sort(key=lambda x: x['created_at'], reverse=True)
print(json.dumps(cals[:2], indent=2))
