from __future__ import annotations

import unittest
import uuid
from pathlib import Path
from typing import Any

from connectors.base import ContextEvent
from execution import ActionExecutor, ApprovalGate, ApprovalStore, AuditLogger, RateLimiter, RiskPolicy, RollbackManager
from memory.database import MemoryDatabase


class FakeRunner:
    def execute_action(self, action: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "echo": {"type": action["type"], "plugin": action["plugin"]}}


class ExecutionTests(unittest.TestCase):
    def build_executor(self, threshold: str = "medium") -> ActionExecutor:
        tmp_path = Path.cwd() / ".test_runs" / str(uuid.uuid4())
        tmp_path.mkdir(parents=True, exist_ok=True)
        database = MemoryDatabase(tmp_path / "memory.sqlite3")
        database.initialize()
        gate = ApprovalGate(ApprovalStore(database), RiskPolicy(threshold))
        executor = ActionExecutor(
            FakeRunner(),  # type: ignore[arg-type]
            gate,
            AuditLogger(database),
            RollbackManager(database),
            RateLimiter(capacity=5),
            database=database,
        )
        executor._test_database = database  # type: ignore[attr-defined]
        return executor

    def test_low_risk_action_is_auto_approved_after_gate(self) -> None:
        executor = self.build_executor()
        result = executor.execute({"type": "draft_email", "plugin": "gmail", "risk": "low"})
        self.assertEqual(result["status"], "executed")

    def test_medium_risk_action_waits_for_approval_then_executes(self) -> None:
        executor = self.build_executor()
        pending = executor.execute({"type": "send_email", "plugin": "gmail", "risk": "medium", "to": "a@example.com"})
        self.assertEqual(pending["status"], "pending_approval")
        request_id = pending["approval_request_id"]
        executor.approval_gate.approve(request_id)
        executed = executor.execute_approved(request_id)
        self.assertEqual(executed["status"], "executed")

    def test_execution_reinforces_source_event(self) -> None:
        executor = self.build_executor(threshold="high")
        database = executor._test_database  # type: ignore[attr-defined]
        event = ContextEvent(source="mcp_gmail", kind="email", title="Reply needed", body="Please reply", importance=0.8)
        database.add_event(event, semantic_summary="Reply needed")
        result = executor.execute({"type": "draft_email", "plugin": "gmail", "risk": "low", "source_event_id": event.id})
        self.assertEqual(result["status"], "executed")
        self.assertGreaterEqual(database.get_event_stats(event.id).execution_count, 1)


if __name__ == "__main__":
    unittest.main()
