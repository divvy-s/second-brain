from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

import uvicorn  # noqa: E402 — must follow load_dotenv() so env vars are set before module init


def main() -> int:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    reload = os.environ.get("ENVIRONMENT", "production").lower() in {"dev", "development", "local"}
    uvicorn.run("api.app:create_app", factory=True, host=host, port=port, reload=reload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

