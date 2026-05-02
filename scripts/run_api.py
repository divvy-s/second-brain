from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

import uvicorn  # noqa: E402 — must follow load_dotenv() so env vars are set before module init


def main() -> int:
    uvicorn.run("api.app:create_app", factory=True, host="127.0.0.1", port=8000, reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

