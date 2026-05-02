from __future__ import annotations

import unittest
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from connectors.base import ContextEvent, utc_now
from execution import ActionExecutor, ApprovalGate, ApprovalRequest, ApprovalStore, AuditLogger, RateLimiter, RiskPolicy, RollbackManager
from intelligence import GoalDecomposer, LLMAdapter, PriorityScorer
from memory import EntityExtractor, MemoryDatabase, VectorStore
from orchestration import BrainWorkflow, ProactiveLoopManager


class LoopRunner:
    def __init__(self, events: list[ContextEvent]) -> None:
        self.events = events

    def fetch_all_events(self) -> list[ContextEvent]:
        return list(self.events)

    def execute_action(self, action: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "action": action["type"], "plugin": action.get("plugin")}


class ProactiveLoopTests(unittest.TestCase):
    def test_proactive_loop_runs_cycle_and_ignores_stale_approvals(self) -> None:
        tmp_path = Path.cwd() / ".test_runs" / str(uuid.uuid4())
        tmp_path.mkdir(parents=True, exist_ok=True)
        database = MemoryDatabase(tmp_path / "memory.sqlite3")
        database.initialize()
        event = ContextEvent(
            source="mcp_gmail",
            kind="email",
            title="Reply to launch update",
            body="Please reply with the latest launch status today.",
            importance=0.85,
        )
        runner = LoopRunner([event])
        gate = ApprovalGate(ApprovalStore(database), RiskPolicy("high"))
        executor = ActionExecutor(
            runner,  # type: ignore[arg-type]
            gate,
            AuditLogger(database),
            RollbackManager(database),
            RateLimiter(capacity=10),
            database=database,
        )
        config = {
            "llm": {
                "primary": {"provider": "xai", "base_url": "https://api.x.ai/v1", "api_key_env": "XAI_API_KEY", "model": "grok-4"},
                "fallback": {"provider": "openrouter", "base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY", "model": "openai/gpt-4.1-mini"},
            }
        }
        workflow = BrainWorkflow(
            runner=runner,  # type: ignore[arg-type]
            database=database,
            vector_store=VectorStore(tmp_path / "vectors"),
            extractor=EntityExtractor(),
            scorer=PriorityScorer(),
            decomposer=GoalDecomposer(LLMAdapter(config)),
            executor=executor,
        )
        stale_request = ApprovalRequest(
            id=str(uuid.uuid4()),
            action={"type": "create_task", "plugin": "todoist", "source_event_id": event.id},
            risk="medium",
            status="pending",
            created_at=(utc_now() - timedelta(minutes=10)).isoformat(),
        )
        gate.store.save(stale_request)
        manager = ProactiveLoopManager(
            services=type("Services", (), {"approval_gate": gate, "database": database})(),  # type: ignore[call-arg]
            workflow_factory=lambda _services: workflow,
            interval_minutes=30,
            stale_approval_minutes=1,
            enabled=True,
        )
        summary = manager.run_cycle()
        self.assertEqual(summary["ignored_approvals"], 1)
        self.assertGreaterEqual(summary["events"], 1)
        self.assertEqual(manager.snapshot()["last_status"], "ok")
        self.assertEqual(len(gate.store.list("ignored")), 1)


if __name__ == "__main__":
    unittest.main()
