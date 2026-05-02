from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

from pathlib import Path

from api.dependencies import build_services, build_workflow


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    services = build_services(root)
    state = build_workflow(services).ingest({})
    print(f"Ingested {len(state.get('events', []))} events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

