from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlsplit

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv()

from connectors.config import load_config
from memory.backends import create_backend
from memory.database import MemoryDatabase


def _describe_postgres_target(database_url: str) -> str:
    parsed = urlsplit(database_url.strip())
    host = parsed.hostname or "configured-host"
    database_name = parsed.path.lstrip("/") or "configured-database"
    return f"{host}/{database_name}"


def main() -> int:
    import os
    root = _ROOT
    config = load_config(root / "config" / "user_config.yml")
    memory_config = config.get("memory", {})
    database_url = os.getenv("DATABASE_URL", memory_config.get("database_url", ""))

    backend = create_backend(database_url)
    db_path = root / memory_config.get("sqlite_path", "data/second_brain.sqlite3")
    if backend.name == "postgres":
        MemoryDatabase(db_path, backend=backend).initialize()
        print(f"Initialized PostgreSQL memory using {_describe_postgres_target(database_url)}")
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        MemoryDatabase(db_path, backend=backend).initialize()
        print(f"Initialized SQLite memory at {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

