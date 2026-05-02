from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable


if TYPE_CHECKING:
    from api.dependencies import AppServices
    from orchestration.workflow import BrainWorkflow


logger = logging.getLogger(__name__)


@dataclass
class ProactiveLoopState:
    """Mutable status for the continuously running orchestration loop."""

    enabled: bool
    interval_minutes: float
    stale_approval_minutes: float
    running: bool = False
    last_started_at: str | None = None
    last_finished_at: str | None = None
    last_status: str = "idle"
    last_error: str = ""
    last_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "interval_minutes": self.interval_minutes,
            "stale_approval_minutes": self.stale_approval_minutes,
            "running": self.running,
            "last_started_at": self.last_started_at,
            "last_finished_at": self.last_finished_at,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "last_summary": self.last_summary,
        }


class ProactiveLoopManager:
    """Run the full ingest-rank-plan pipeline on a fixed interval."""

    def __init__(
        self,
        services: AppServices,
        workflow_factory: Callable[[AppServices], BrainWorkflow],
        *,
        interval_minutes: float = 30.0,
        stale_approval_minutes: float = 180.0,
        enabled: bool = True,
    ) -> None:
        self.services = services
        self.workflow_factory = workflow_factory
        self.state = ProactiveLoopState(
            enabled=enabled,
            interval_minutes=max(1.0, float(interval_minutes)),
            stale_approval_minutes=max(1.0, float(stale_approval_minutes)),
        )
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Start the background loop once when the API boots."""
        if not self.state.enabled or (self._thread is not None and self._thread.is_alive()):
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_forever, name="second-brain-proactive-loop", daemon=True)
        self._thread.start()

    def stop(self, timeout_seconds: float = 5.0) -> None:
        """Stop the background loop cleanly on shutdown."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=max(0.1, float(timeout_seconds)))

    def run_cycle(self) -> dict[str, Any]:
        """Execute one full proactive orchestration cycle immediately."""
        self.state.running = True
        self.state.last_status = "running"
        self.state.last_error = ""
        self.state.last_started_at = datetime.now(timezone.utc).isoformat()
        try:
            ignored = self.services.approval_gate.ignore_stale(self.state.stale_approval_minutes)
            workflow = self.workflow_factory(self.services)
            result = workflow.run()
            summary = {
                "ignored_approvals": len(ignored),
                "events": len(result.get("events", [])),
                "prioritized": len(result.get("prioritized", [])),
                "recommendations": len(result.get("recommendations", [])),
                "actions": len(result.get("actions", [])),
                "results": len(result.get("results", [])),
            }
            self.state.last_summary = summary
            self.state.last_status = "ok"
            self.services.database.set_setting("proactive_loop_last_summary", summary)
            return summary
        except Exception as exc:
            self.state.last_status = "error"
            self.state.last_error = str(exc)
            logger.exception("Proactive loop cycle failed")
            raise
        finally:
            self.state.running = False
            self.state.last_finished_at = datetime.now(timezone.utc).isoformat()

    def snapshot(self) -> dict[str, Any]:
        """Expose a serializable status payload for health and diagnostics."""
        return self.state.to_dict()

    def _run_forever(self) -> None:
        self.run_cycle()
        while not self._stop_event.wait(self.state.interval_minutes * 60.0):
            self.run_cycle()
