from __future__ import annotations

import unittest
import uuid
from pathlib import Path
from typing import Any

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
        return ActionExecutor(
            FakeRunner(),  # type: ignore[arg-type]
            gate,
            AuditLogger(database),
            RollbackManager(database),
            RateLimiter(capacity=5),
        )

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


if __name__ == "__main__":
    unittest.main()
