import sqlite3
import sys
sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect('data/second_brain.sqlite3')
cur = conn.cursor()

# Check schema
cur.execute("PRAGMA table_info(context_events)")
print("=== Schema ===")
for r in cur.fetchall():
    print(f"  {r[1]} ({r[2]})")

# Count events by source
cur.execute("SELECT source, kind, COUNT(*) FROM context_events GROUP BY source, kind ORDER BY source")
rows = cur.fetchall()
print("\n=== Events by Source ===")
for r in rows:
    print(f"  {r[0]} / {r[1]}: {r[2]}")

# Show recent emails
print("\n=== Recent Emails ===")
cur.execute("SELECT title, body, occurred_at FROM context_events WHERE source = 'mcp_gmail' ORDER BY occurred_at DESC LIMIT 5")
for r in cur.fetchall():
    print(f"  [{r[2][:16]}] {r[0][:60]}")
    print(f"    {r[1][:80]}")

# Show calendar events
print("\n=== Calendar Events ===")
cur.execute("SELECT title, body, occurred_at FROM context_events WHERE source = 'mcp_calendar' ORDER BY occurred_at DESC LIMIT 5")
rows = cur.fetchall()
if rows:
    for r in rows:
        print(f"  [{r[2][:16]}] {r[0][:60]}")
else:
    print("  (none found)")

# Show telegram events
print("\n=== Telegram Events ===")
cur.execute("SELECT title, body, occurred_at FROM context_events WHERE source = 'mcp_telegram' ORDER BY occurred_at DESC LIMIT 5")
rows = cur.fetchall()
if rows:
    for r in rows:
        print(f"  [{r[2][:16]}] {r[0][:60]}")
        print(f"    {r[1][:80]}")
else:
    print("  (none found)")

print("\nTotal events: ", end="")
cur.execute("SELECT COUNT(*) FROM context_events")
print(cur.fetchone()[0])
