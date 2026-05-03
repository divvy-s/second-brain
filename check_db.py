import sqlite3
import json

conn = sqlite3.connect('data/second_brain.sqlite3')
c = conn.cursor()

c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in c.fetchall()]
print("Tables:", tables)

if "audit_logs" in tables:
    c.execute("SELECT action_id, plugin, status, result FROM audit_logs ORDER BY timestamp DESC LIMIT 5")
    print("Audit logs:")
    for row in c.fetchall():
        print(row)
elif "actions" in tables:
    c.execute("SELECT * FROM actions ORDER BY id DESC LIMIT 5")
    print("Actions:")
    for row in c.fetchall():
        print(row)
