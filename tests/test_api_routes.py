from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
    import api.app as app_module
except ModuleNotFoundError:
    TestClient = None  # type: ignore[assignment]
    app_module = None  # type: ignore[assignment]
from connectors.base import ContextEvent
from execution import ActionExecutor, ApprovalGate, ApprovalStore, AuditLogger, RateLimiter, RiskPolicy, RollbackManager
from intelligence import BrainDumpCapture, IntentClassifier, LLMAdapter, PriorityScorer
from memory import EntityExtractor, EventBus, HybridRetriever, MemoryDatabase, MemoryDecay


class FakeConnector:
    def __init__(self, events: list[ContextEvent] | None = None, status: dict[str, Any] | None = None) -> None:
        self.events = events or []
        self.status = status or {"healthy": True, "mode": "live"}
        self.config: dict[str, Any] = {}

    def fetch_events(self) -> list[ContextEvent]:
        return list(self.events)

    def health_status(self) -> dict[str, Any]:
        return dict(self.status)

    def execute_action(self, action: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "action": action}


class FakeRunner:
    def __init__(self) -> None:
        self.last_fetch_failures: dict[str, str] = {}
        self.connectors = {
            "slack": FakeConnector([ContextEvent(source="slack", kind="message", title="Sync item", body="From Slack")]),
            "todoist": FakeConnector([ContextEvent(source="todoist", kind="task", title="Ship checklist", body="")]),
            "calendar": FakeConnector([ContextEvent(source="calendar", kind="event", title="Planning", body="")]),
        }

    def fetch_all_events(self) -> list[ContextEvent]:
        events: list[ContextEvent] = []
        for connector in self.connectors.values():
            events.extend(connector.fetch_events())
        return events

    def health(self) -> dict[str, dict[str, Any]]:
        return {name: connector.health_status() for name, connector in self.connectors.items()}

    def execute_action(self, action: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "action": action}


class FakeVectorStore:
    ready = False

    async def initialize_async(self) -> None:
        return None

    def index_event(self, event: ContextEvent, *, summary: str | None = None) -> None:
        return None

    def query(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        return []


def build_test_services(tmp_path: Path) -> Any:
    database = MemoryDatabase(tmp_path / "memory.sqlite3")
    database.initialize()
    vector_store = FakeVectorStore()
    extractor = EntityExtractor()
    scorer = PriorityScorer()
    decay = MemoryDecay()
    llm = LLMAdapter({})
    capture = BrainDumpCapture(extractor, scorer)
    retriever = HybridRetriever(database, vector_store, decay=decay, scorer=scorer, extractor=extractor)
    runner = FakeRunner()
    approval_gate = ApprovalGate(ApprovalStore(database), RiskPolicy("medium"))
    executor = ActionExecutor(
        runner,  # type: ignore[arg-type]
        approval_gate,
        AuditLogger(database),
        RollbackManager(database),
        RateLimiter(capacity=10),
        database=database,
    )
    config = {
        "api": {"auth_token_env": "SECRET_KEY"},
        "plugins": {
            "whatsapp": {"verify_token": "verify-me"},
            "telegram": {"webhook_secret": "telegram-secret", "inbound_mode": "webhook", "polling_enabled": False},
        },
        "rate_limits": {"enabled": True, "expensive_capacity": 2, "expensive_refill_per_second": 0.0},
    }
    return SimpleNamespace(
        root=tmp_path,
        config=config,
        registry=SimpleNamespace(list_plugins=lambda: []),
        runner=runner,
        database=database,
        vector_store=vector_store,
        event_bus=EventBus("redis://localhost:1/0"),
        extractor=extractor,
        scorer=scorer,
        llm=llm,
        capture=capture,
        retriever=retriever,
        approval_gate=approval_gate,
        executor=executor,
        classifier=IntentClassifier(llm),
        refresh_plugins=lambda: None,
    )


@unittest.skipIf(TestClient is None, "fastapi is not installed")
class ApiRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.services = build_test_services(Path(self.tmp.name))
        self.env_patch = patch.dict("os.environ", {"ENVIRONMENT": "development", "SECRET_KEY": ""}, clear=False)
        self.env_patch.start()
        self.services_patch = patch.object(app_module, "build_services", lambda: self.services)
        self.services_patch.start()
        app_module._chroma_ready.clear()
        self.client_context = TestClient(app_module.create_app())  # type: ignore[misc]
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.services_patch.stop()
        self.env_patch.stop()
        self.tmp.cleanup()

    def test_events_sync_stores_connector_events_without_orchestration(self) -> None:
        response = self.client.post("/events/sync")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["synced"], 3)
        self.assertIsNotNone(self.services.database.get_event(payload["events"][0]["id"]))
        feed = self.client.get("/events/feed?limit=2&offset=0").json()
        self.assertEqual(feed["total"], 3)
        self.assertTrue(feed["has_more"])

    def test_brain_dump_and_approval_routes(self) -> None:
        response = self.client.post("/brain-dump", json={"text": "todo review launch notes"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["event"]["kind"], "brain_dump")
        self.assertTrue(payload["approvals"])
        approvals = self.client.get("/approvals/list?status=pending").json()
        self.assertEqual(len(approvals["approvals"]), 1)

    def test_orchestrate_route_returns_workflow_state(self) -> None:
        response = self.client.post(
            "/orchestrate",
            json={"events": [{"source": "slack", "kind": "message", "title": "Urgent launch", "body": "needs approval today"}]},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["events"])
        self.assertIn("prioritized", payload)
        self.assertIn("results", payload)

    def test_tasks_and_schedule_connected_states(self) -> None:
        self.assertTrue(self.client.get("/tasks/pending").json()["connected"])
        self.assertTrue(self.client.get("/schedule/upcoming").json()["connected"])
        self.services.runner.connectors["calendar"] = FakeConnector(
            status={"healthy": False, "mode": "unconfigured", "mock_enabled": False, "error": "Calendar is not connected."}
        )
        calendar = self.client.get("/schedule/upcoming").json()
        self.assertFalse(calendar["connected"])
        self.assertEqual(calendar["source"], "unconfigured")

    def test_whatsapp_verification_and_message_flow(self) -> None:
        verify = self.client.get(
            "/whatsapp/webhook",
            params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "12345"},
        )
        self.assertEqual(verify.status_code, 200)
        self.assertEqual(verify.text, "12345")
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {"id": "wamid.1", "from": "15551234567", "type": "text", "text": {"body": "urgent hello"}}
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        posted = self.client.post("/whatsapp/webhook", json=payload)
        self.assertEqual(posted.status_code, 200)
        self.assertEqual(posted.json()["processed"], 1)
        self.assertIsNotNone(self.services.database.get_event("whatsapp-wamid.1"))

    def test_telegram_webhook_secret_and_capture(self) -> None:
        denied = self.client.post("/telegram/webhook", json={"update_id": 1, "message": {"text": "hello"}})
        self.assertEqual(denied.status_code, 401)
        setup = self.client.get("/telegram/webhook/setup?public_url=https://abc123.ngrok-free.app")
        self.assertEqual(setup.status_code, 200)
        self.assertIn("secret_token=telegram-secret", setup.json()["set_webhook_url"])
        allowed = self.client.post(
            "/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
            json={"update_id": 2, "message": {"text": "capture this", "from": {"id": 7}, "chat": {"id": 9}}},
        )
        self.assertEqual(allowed.status_code, 200)
        event_id = allowed.json()["event"]["id"]
        self.assertIsNotNone(self.services.database.get_event(event_id))
        query_secret = self.client.post(
            "/telegram/webhook?secret_token=telegram-secret",
            json={"update_id": 3, "message": {"text": "query token works", "from": {"id": 7}, "chat": {"id": 9}}},
        )
        self.assertEqual(query_secret.status_code, 200)

    def test_expensive_endpoint_rate_limit(self) -> None:
        for index in range(2):
            self.assertEqual(self.client.post("/brain-dump", json={"text": f"todo item {index}"}).status_code, 200)
        self.assertEqual(self.client.post("/brain-dump", json={"text": "todo item 3"}).status_code, 429)


if __name__ == "__main__":
    unittest.main()
