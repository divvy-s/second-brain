from __future__ import annotations

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv()

from api.dependencies import build_services, build_workflow
from orchestration import ProactiveLoopManager


def _env_bool(name: str, default: bool) -> bool:
    import os

    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _env_float(name: str, default: float) -> float:
    import os

    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _proactive_loop_settings(config: dict[str, object]) -> dict[str, float | bool]:
    orchestration_config = config.get("orchestration", {}) if isinstance(config, dict) else {}
    if not isinstance(orchestration_config, dict):
        orchestration_config = {}
    return {
        "enabled": _env_bool(
            "SECOND_BRAIN_PROACTIVE_LOOP_ENABLED",
            bool(orchestration_config.get("proactive_loop_enabled", True)),
        ),
        "interval_minutes": max(
            1.0,
            _env_float(
                "SECOND_BRAIN_PROACTIVE_LOOP_INTERVAL_MINUTES",
                float(orchestration_config.get("proactive_loop_interval_minutes", 30.0)),
            ),
        ),
        "stale_approval_minutes": max(
            1.0,
            _env_float(
                "SECOND_BRAIN_STALE_APPROVAL_MINUTES",
                float(orchestration_config.get("stale_approval_minutes", 180.0)),
            ),
        ),
    }


def main() -> int:
    """Run the proactive orchestration loop as a standalone worker."""
    services = build_services(_ROOT)
    settings = _proactive_loop_settings(services.config)
    manager = ProactiveLoopManager(
        services,
        build_workflow,
        interval_minutes=float(settings["interval_minutes"]),
        stale_approval_minutes=float(settings["stale_approval_minutes"]),
        enabled=bool(settings["enabled"]),
    )
    manager.start()
    print("Proactive loop started")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        manager.stop()
        print("Proactive loop stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
