from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv()

import uvicorn


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def main() -> int:
    """Run the FastAPI app using env-configurable host/port settings."""
    host = os.getenv("HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.getenv("PORT", "8000"))
    reload_enabled = _env_flag("DEBUG", False)
    uvicorn.run("api.app:create_app", factory=True, host=host, port=port, reload=reload_enabled)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

