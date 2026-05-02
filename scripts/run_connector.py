from __future__ import annotations

import json

from connectors.runner import ConnectorRunner


def main() -> int:
    runner = ConnectorRunner()
    events = [event.to_dict() for event in runner.fetch_all_events()]
    print(json.dumps(events, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

