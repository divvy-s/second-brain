import sqlite3
import sys

conn = sqlite3.connect("data/second_brain.sqlite3")
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print("DB tables:", tables)
if not tables:
    print("DB EMPTY - needs init")
    sys.exit(1)
else:
    print("DB OK")
