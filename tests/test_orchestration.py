from __future__ import annotations

import unittest
import uuid
from pathlib import Path
from typing import Any

from connectors.base import ContextEvent
from execution import ActionExecutor, ApprovalGate, ApprovalStore, AuditLogger, RateLimiter, RiskPolicy, RollbackManager
from intelligence import GoalDecomposer, LLMAdapter, PriorityScorer
from memory import EntityExtractor, MemoryDatabase, VectorStore
from orchestration import BrainWorkflow


class EmptyRunner:
    def fetch_all_events(self) -> list[ContextEvent]:
        return []

    def execute_action(self, action: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "action": action["type"]}


class OrchestrationTests(unittest.TestCase):
    def test_workflow_ingests_prioritizes_and_gates_actions(self) -> None:
        tmp_path = Path.cwd() / ".test_runs" / str(uuid.uuid4())
        tmp_path.mkdir(parents=True, exist_ok=True)
        database = MemoryDatabase(tmp_path / "memory.sqlite3")
        database.initialize()
        gate = ApprovalGate(ApprovalStore(database), RiskPolicy("medium"))
        executor = ActionExecutor(
            EmptyRunner(),  # type: ignore[arg-type]
            gate,
            AuditLogger(database),
            RollbackManager(database),
            RateLimiter(capacity=10),
        )
        config = {
            "llm": {
                "primary": {"provider": "xai", "base_url": "https://api.x.ai/v1", "api_key_env": "XAI_API_KEY", "model": "grok-4"},
                "fallback": {"provider": "openrouter", "base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY", "model": "openai/gpt-4.1-mini"},
            }
        }
        workflow = BrainWorkflow(
            runner=EmptyRunner(),  # type: ignore[arg-type]
            database=database,
            vector_store=VectorStore(tmp_path / "vectors"),
            extractor=EntityExtractor(),
            scorer=PriorityScorer(),
            decomposer=GoalDecomposer(LLMAdapter(config)),
            executor=executor,
        )
        event = ContextEvent(
            source="mcp_slack",
            kind="message",
            title="Launch blocked",
            body="The launch is blocked and needs approval today.",
            importance=0.8,
        )
        state = workflow.run([event])
        self.assertTrue(state["prioritized"])
        self.assertTrue(state["retrievals"])
        self.assertTrue(state["recommendations"])
        self.assertTrue(state["actions"])
        self.assertTrue(state["results"])
        self.assertGreaterEqual(state["prioritized"][0].score, 0.0)


if __name__ == "__main__":
    unittest.main()
