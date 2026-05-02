from __future__ import annotations

from pathlib import Path

from connectors.config import load_config
from memory.database import MemoryDatabase


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "config" / "user_config.yml")
    db_path = root / config.get("memory", {}).get("sqlite_path", "data/second_brain.sqlite3")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    MemoryDatabase(db_path).initialize()
    print(f"Initialized SQLite memory at {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

