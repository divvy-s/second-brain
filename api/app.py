from __future__ import annotations

import asyncio
import json as _json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from secrets import compare_digest
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.dependencies import AppServices, build_services, build_workflow
from api.routes.auth import router as auth_router
from api.schemas import (
    ActionRequest,
    ApprovalDecisionRequest,
    BrainDumpRequest,
    OrchestrateRequest,
    PluginInstallRequest,
    PluginNameRequest,
    RetrievalRequest,
    TelegramWebhookRequest,
)
from api.telegram_bot import TelegramApprovalBot
from connectors.base import ContextEvent


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WebSocket Connection Manager — broadcasts events to all connected clients
# ---------------------------------------------------------------------------
class ConnectionManager:
    def __init__(self) -> None:
        self._active: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._active.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._active.discard(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in self._active:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._active.discard(ws)


ws_manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _retrieval_hit_to_json(hit: Any) -> dict[str, Any]:
    return {
        "event": hit.event.to_dict(),
        "score": hit.score,
        "vector_score": hit.vector_score,
        "keyword_score": hit.keyword_score,
        "priority_score": hit.priority_score,
        "decay_factor": hit.decay_factor,
        "reinforcement": hit.reinforcement,
        "summary": hit.summary,
        "components": hit.components,
    }


def _state_to_json(state: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in state.items():
        if key == "events":
            output[key] = [event.to_dict() for event in value]
        elif key == "prioritized":
            output[key] = [_retrieval_hit_to_json(hit) for hit in value]
        elif key == "retrievals":
            output[key] = [
                {
                    "event_id": item.get("event_id"),
                    "score": item.get("score"),
                    "supporting_hits": [_retrieval_hit_to_json(hit) for hit in item.get("supporting_hits", [])],
                    "structured": item.get("structured", {}),
                }
                for item in value
            ]
        else:
            output[key] = value
    return output


def _allowed_origins(config: dict[str, Any]) -> list[str]:
    env_origins = os.environ.get("ALLOWED_ORIGINS", "").strip()
    if env_origins:
        return [item.strip() for item in env_origins.split(",") if item.strip()]
    config_origins = config.get("api", {}).get("allowed_origins", [])
    if isinstance(config_origins, list) and config_origins:
        return [str(item) for item in config_origins]
    return ["http://localhost:5173", "http://127.0.0.1:5173"]


def _api_token(config: dict[str, Any]) -> str:
    api_config = config.get("api", {})
    env_name = str(api_config.get("auth_token_env", "SECRET_KEY"))
    return str(os.environ.get(env_name, "")).strip()


def _telegram_secret(config: dict[str, Any]) -> str:
    telegram_config = config.get("plugins", {}).get("telegram", {})
    env_name = str(telegram_config.get("webhook_secret_env", "TELEGRAM_WEBHOOK_SECRET"))
    return str(os.environ.get(env_name, "")).strip() or str(telegram_config.get("webhook_secret", "")).strip()


# ---------------------------------------------------------------------------
# ChromaDB readiness flag — set after background initialization completes
# ---------------------------------------------------------------------------
_chroma_ready = asyncio.Event()


# ---------------------------------------------------------------------------
# App Factory
# ---------------------------------------------------------------------------
def create_app() -> FastAPI:
    services_obj = build_services()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async def init_chroma():
            await services_obj.vector_store.initialize_async()
            _chroma_ready.set()

        async def poll_connectors():
            """Background task: poll live connectors every 30s and broadcast new events."""
            import sqlite3 as _sqlite3
            seen_ids: set[str] = set()
            # Pre-seed seen_ids from DB so we don't re-alert on startup
            try:
                conn = services_obj.database.connect()
                for row in conn.execute("SELECT id FROM context_events"):
                    seen_ids.add(row[0])
            except Exception:
                pass

            POLL_SOURCES = ["telegram", "slack", "gmail"]
            while True:
                await asyncio.sleep(30)
                try:
                    svc = services_obj
                    for source in POLL_SOURCES:
                        connector = svc.runner.connectors.get(source)
                        if connector is None:
                            continue
                        try:
                            events = connector.fetch_events()
                        except Exception:
                            continue
                        for event in events:
                            if event.id in seen_ids:
                                continue
                            seen_ids.add(event.id)
                            # Ingest the new event into DB
                            try:
                                workflow = build_workflow(svc)
                                await workflow.ingest({"events": [event]})
                            except Exception:
                                pass
                            payload = event.to_dict()
                            # Broadcast new event
                            await ws_manager.broadcast({"type": "new_event", "payload": payload})
                            # Check for urgent WhatsApp message
                            if source == "whatsapp":
                                text = (event.title or "") + " " + (event.body or "")
                                if "urgent" in text.lower():
                                    await ws_manager.broadcast({
                                        "type": "urgent_alert",
                                        "payload": {
                                            "source": "whatsapp",
                                            "title": "Urgent WhatsApp Message",
                                            "body": event.body or event.title,
                                        }
                                    })
                except Exception as exc:
                    logger.warning("Connector poll error: %s", exc)

        asyncio.create_task(init_chroma())
        asyncio.create_task(poll_connectors())
        yield

    app = FastAPI(title="Second Brain", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins(services_obj.config),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include OAuth2 router
    app.include_router(auth_router)

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        import traceback

        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal Server Error", "error": str(exc)},
        )

    app.state.services = services_obj

    def services() -> AppServices:
        return app.state.services

    # ── Auth Middleware ────────────────────────────────────────────────
    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        public_paths = {"/health", "/docs", "/openapi.json", "/redoc", "/telegram/webhook",
                        "/auth/google", "/auth/google/callback",
                        "/whatsapp/webhook"}
        if request.method.upper() == "OPTIONS" or request.url.path in public_paths:
            return await call_next(request)
        # WebSocket upgrade requests bypass HTTP auth (handled separately)
        if request.url.path.startswith("/ws/"):
            return await call_next(request)
        # Dev-mode bypass: skip auth when ENVIRONMENT=development and no token configured
        token = _api_token(services().config)
        is_dev = os.environ.get("ENVIRONMENT", "development").lower() == "development"
        if not token and is_dev:
            return await call_next(request)
        if not token:
            return JSONResponse(status_code=503, content={"detail": "API authentication is not configured"})
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"detail": "Missing Bearer token"})
        provided = authorization.removeprefix("Bearer ").strip()
        if not compare_digest(provided, token):
            return JSONResponse(status_code=403, content={"detail": "Invalid API token"})
        return await call_next(request)

    # ── Health ─────────────────────────────────────────────────────────
    @app.get("/health")
    def health() -> dict[str, Any]:
        svc = services()
        return {
            "ok": True,
            "plugins": svc.runner.health(),
            "plugin_failures": svc.runner.last_fetch_failures,
            "redis_backed": svc.event_bus.is_redis_backed,
            "llm_configured": svc.llm.is_configured(),
            "api_auth_configured": bool(_api_token(svc.config)),
            "chroma_ready": _chroma_ready.is_set(),
        }

    # ── Plugins ────────────────────────────────────────────────────────
    @app.get("/plugins/list")
    def plugins_list() -> dict[str, Any]:
        return {"plugins": services().registry.list_plugins()}

    # ── Events Feed ────────────────────────────────────────────────────
    @app.get("/events/feed")
    def events_feed(limit: int = 30, source: str | None = None) -> dict[str, Any]:
        """Return recent context events for the activity feed."""
        svc = services()
        db = svc.database
        conn = db.connect()
        cursor = conn.cursor()
        if source:
            cursor.execute(
                "SELECT id, source, kind, title, body, occurred_at, participants_json, importance, metadata_json, semantic_summary "
                "FROM context_events WHERE source = ? ORDER BY occurred_at DESC LIMIT ?",
                (source, limit),
            )
        else:
            cursor.execute(
                "SELECT id, source, kind, title, body, occurred_at, participants_json, importance, metadata_json, semantic_summary "
                "FROM context_events ORDER BY occurred_at DESC LIMIT ?",
                (limit,),
            )
        events = []
        for row in cursor.fetchall():
            events.append({
                "id": row[0],
                "source": row[1],
                "kind": row[2],
                "title": row[3],
                "body": row[4],
                "occurred_at": row[5],
                "participants": _json.loads(row[6]) if row[6] else [],
                "importance": row[7],
                "metadata": _json.loads(row[8]) if row[8] else {},
                "semantic_summary": row[9],
            })
        return {"events": events, "total": len(events)}

    # ── Plugin Management ──────────────────────────────────────────────
    @app.post("/plugins/install")
    def plugins_install(request: PluginInstallRequest) -> dict[str, Any]:
        try:
            result = services().registry.install_plugin(request.path)
            services().refresh_plugins()
            return result
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/plugins/enable")
    def plugins_enable(request: PluginNameRequest) -> dict[str, Any]:
        result = services().registry.set_plugin_enabled(request.name, True)
        services().refresh_plugins()
        return result

    @app.post("/plugins/disable")
    def plugins_disable(request: PluginNameRequest) -> dict[str, Any]:
        result = services().registry.set_plugin_enabled(request.name, False)
        services().refresh_plugins()
        return result

    # ── Ingestion ──────────────────────────────────────────────────────
    @app.post("/events/ingest")
    async def ingest(request: OrchestrateRequest | None = None) -> dict[str, Any]:
        svc = services()
        workflow = build_workflow(svc)
        events = [ContextEvent.from_dict(item) for item in request.events] if request and request.events else None
        state = await workflow.ingest({"events": events or []}) if events is not None else await workflow.ingest({})
        ingested = state.get("events", [])
        # Broadcast new events to WebSocket clients
        for event in ingested:
            await ws_manager.broadcast({"type": "new_event", "payload": event.to_dict()})
        return {"events": [event.to_dict() for event in ingested]}

    @app.post("/events/retrieve")
    def retrieve(request: RetrievalRequest) -> dict[str, Any]:
        result = services().retriever.retrieve(request.query, request.limit)
        return {
            "hits": [_retrieval_hit_to_json(hit) for hit in result.hits],
            "structured": result.structured.to_dict(),
        }

    # ── Brain Dump (async for LLM calls) ──────────────────────────────
    @app.post("/brain-dump")
    async def brain_dump(request: BrainDumpRequest) -> dict[str, Any]:
        svc = services()
        workflow = build_workflow(svc)

        event = svc.capture.capture(request.text, title=request.title)
        stored_state = await workflow.ingest({"events": [event]})
        stored_events = stored_state.get("events", [])
        if not stored_events:
            raise HTTPException(status_code=500, detail="Brain dump ingestion produced no stored events")
        stored_event = stored_events[0]

        intents = await svc.classifier.classify(request.text)
        intent_responses = []
        for intent in intents:
            action = svc.classifier.to_action(intent, source_event_id=stored_event.id)
            svc.approval_gate.evaluate(action)
            intent_responses.append(
                {
                    "type": intent.intent,
                    "plugin": intent.plugin,
                    "confidence": intent.confidence,
                    "fields": intent.fields,
                    "reasoning": intent.reasoning,
                }
            )

        pending = [
            asdict(request_item)
            for request_item in svc.approval_gate.store.list("pending")
            if str(request_item.action.get("source_event_id")) == stored_event.id
        ]

        # Broadcast new event and approvals to WebSocket clients
        await ws_manager.broadcast({"type": "new_event", "payload": stored_event.to_dict()})
        for approval in pending:
            await ws_manager.broadcast({"type": "new_approval", "payload": approval})

        return {
            "event": stored_event.to_dict(),
            "intents": intent_responses,
            "approvals": pending,
        }

    # ── Orchestration ──────────────────────────────────────────────────
    @app.post("/orchestrate")
    async def orchestrate(request: OrchestrateRequest | None = None) -> dict[str, Any]:
        svc = services()
        events = [ContextEvent.from_dict(item) for item in request.events] if request and request.events else None
        workflow = build_workflow(svc)
        state = await workflow.run(events)
        return _state_to_json(state)

    # ── Action Execution ───────────────────────────────────────────────
    @app.post("/actions/execute")
    def execute_action(request: ActionRequest) -> dict[str, Any]:
        return services().executor.execute(request.action)

    # ── Approvals ──────────────────────────────────────────────────────
    @app.get("/approvals/list")
    def approvals_list(status: str | None = None) -> dict[str, Any]:
        return {"approvals": [asdict(request_item) for request_item in services().approval_gate.store.list(status)]}

    @app.post("/approvals/approve")
    async def approvals_approve(request: ApprovalDecisionRequest) -> dict[str, Any]:
        try:
            approved = services().approval_gate.approve(request.request_id)
            execution = services().executor.execute_approved(approved.id)
            result = {"request": asdict(approved), "execution": execution}
            # Notify WebSocket clients
            await ws_manager.broadcast({"type": "approval_decided", "payload": {"id": request.request_id, "status": "approved"}})
            return result
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/approvals/reject")
    async def approvals_reject(request: ApprovalDecisionRequest) -> dict[str, Any]:
        try:
            rejected = services().approval_gate.reject(request.request_id)
            result = {"request": asdict(rejected)}
            await ws_manager.broadcast({"type": "approval_decided", "payload": {"id": request.request_id, "status": "rejected"}})
            return result
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # ── Telegram Webhook ───────────────────────────────────────────────
    @app.post("/telegram/webhook")
    async def telegram_webhook(raw_request: Request, request: TelegramWebhookRequest) -> dict[str, Any]:
        expected_secret = _telegram_secret(services().config)
        if expected_secret:
            provided_secret = raw_request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if not compare_digest(provided_secret, expected_secret):
                raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret")
        result = TelegramApprovalBot(services()).handle_update(request.update)
        # Broadcast webhook event to frontend
        await ws_manager.broadcast({"type": "telegram_webhook", "payload": result})
        return result

    # ── Live Tasks & Schedule ──────────────────────────────────────────
    @app.get("/tasks/pending")
    def tasks_pending() -> dict[str, Any]:
        """Fetch live pending tasks from Todoist."""
        svc = services()
        connector = svc.runner.connectors.get("todoist")
        if connector is None:
            return {"tasks": [], "source": "disabled"}
        try:
            events = connector.fetch_events()
            tasks = [
                {
                    "id": e.id,
                    "title": e.title,
                    "body": e.body,
                    "occurred_at": e.occurred_at.isoformat() if hasattr(e.occurred_at, 'isoformat') else str(e.occurred_at),
                    "importance": e.importance,
                    "metadata": e.metadata,
                }
                for e in events
                if e.kind == "task"
            ]
            return {"tasks": tasks, "source": "live"}
        except Exception as exc:
            return {"tasks": [], "source": "error", "error": str(exc)}

    @app.get("/schedule/upcoming")
    def schedule_upcoming() -> dict[str, Any]:
        """Fetch live upcoming calendar events."""
        svc = services()
        connector = svc.runner.connectors.get("calendar")
        if connector is None:
            return {"events": [], "source": "disabled"}
        try:
            events = connector.fetch_events()
            schedule = [
                {
                    "id": e.id,
                    "title": e.title,
                    "body": e.body,
                    "occurred_at": e.occurred_at.isoformat() if hasattr(e.occurred_at, 'isoformat') else str(e.occurred_at),
                    "participants": e.participants,
                    "importance": e.importance,
                    "metadata": e.metadata,
                }
                for e in events
            ]
            return {"events": schedule, "source": "live"}
        except Exception as exc:
            return {"events": [], "source": "error", "error": str(exc)}

    # ── WhatsApp Cloud API Webhook ──────────────────────────────────────
    @app.get("/whatsapp/webhook")
    async def whatsapp_webhook_verify(request: Request) -> Any:
        """WhatsApp Cloud API sends a GET to verify the webhook URL."""
        params = dict(request.query_params)
        mode = params.get("hub.mode")
        token = params.get("hub.verify_token")
        challenge = params.get("hub.challenge")
        expected = os.environ.get("WHATSAPP_VERIFY_TOKEN", "second_brain_verify")
        if mode == "subscribe" and token == expected:
            return JSONResponse(content=int(challenge) if challenge and challenge.isdigit() else challenge)
        return JSONResponse(status_code=403, content={"detail": "Verification failed"})

    @app.post("/whatsapp/webhook")
    async def whatsapp_webhook_receive(request: Request) -> dict[str, Any]:
        """WhatsApp Cloud API POSTs incoming messages here."""
        try:
            body = await request.json()
        except Exception:
            return {"ok": False}
        # Parse the WhatsApp Cloud API message format
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for message in value.get("messages", []):
                    msg_id = message.get("id", "")
                    from_number = message.get("from", "")
                    msg_text = ""
                    if message.get("type") == "text":
                        msg_text = message.get("text", {}).get("body", "")
                    elif message.get("type") == "image":
                        msg_text = message.get("image", {}).get("caption", "[image]")
                    event_id = f"whatsapp-{msg_id}"
                    event = ContextEvent(
                        id=event_id,
                        source="mcp_whatsapp",
                        kind="message",
                        title=f"WhatsApp from {from_number}",
                        body=msg_text,
                        participants=[from_number],
                        importance=0.75,
                        metadata={"message_id": msg_id, "from": from_number},
                    )
                    # Store & broadcast
                    try:
                        svc = services()
                        workflow = build_workflow(svc)
                        await workflow.ingest({"events": [event]})
                    except Exception:
                        pass
                    await ws_manager.broadcast({"type": "new_event", "payload": event.to_dict()})
                    # Urgent alert
                    if "urgent" in msg_text.lower():
                        await ws_manager.broadcast({
                            "type": "urgent_alert",
                            "payload": {
                                "source": "whatsapp",
                                "title": "🚨 Urgent WhatsApp Message",
                                "body": msg_text,
                            }
                        })
        return {"ok": True}

    # ── WebSocket Endpoint ─────────────────────────────────────────────
    @app.websocket("/ws/events")
    async def websocket_events(websocket: WebSocket):
        await ws_manager.connect(websocket)
        try:
            while True:
                # Keep the connection alive; client can also send pings
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            ws_manager.disconnect(websocket)
        except Exception:
            ws_manager.disconnect(websocket)

    return app
