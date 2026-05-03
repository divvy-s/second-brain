from __future__ import annotations

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from dataclasses import asdict
from secrets import compare_digest
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

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
)
from api.telegram_bot import TelegramApprovalBot
from api.webhook_security import (
    build_telegram_webhook_setup,
    ensure_telegram_webhook_secret,
    read_telegram_webhook_secret,
    telegram_webhook_mode,
    telegram_webhook_status,
)
from api.whatsapp_webhook import extract_whatsapp_events
from connectors.base import ContextEvent
from execution import RateLimiter
from intelligence.llm_adapter import LLMUnavailable


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
        return [item.strip().rstrip("/") for item in env_origins.split(",") if item.strip()]
    config_origins = config.get("api", {}).get("allowed_origins", [])
    if isinstance(config_origins, list) and config_origins:
        return [str(item).rstrip("/") for item in config_origins]
    return ["http://localhost:5173", "http://127.0.0.1:5173"]


def _api_token(config: dict[str, Any]) -> str:
    api_config = config.get("api", {})
    env_name = str(api_config.get("auth_token_env", "SECRET_KEY"))
    return str(os.environ.get(env_name, "")).strip()


def _environment() -> str:
    return os.environ.get("ENVIRONMENT", "production").strip().lower() or "production"


def _is_development() -> bool:
    return _environment() in {"dev", "development", "local", "test"}


def _telegram_secret(config: dict[str, Any]) -> str:
    return read_telegram_webhook_secret(config).secret


def _provided_telegram_secret(request: Request) -> str:
    header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    query_secret = request.query_params.get("secret_token", "")
    return str(header_secret or query_secret or "").strip()


def _whatsapp_verify_token(config: dict[str, Any]) -> str:
    whatsapp_config = config.get("plugins", {}).get("whatsapp", {})
    env_name = str(whatsapp_config.get("verify_token_env", "WHATSAPP_VERIFY_TOKEN"))
    return str(os.environ.get(env_name, "")).strip() or str(whatsapp_config.get("verify_token", "")).strip()


def _safe_error(exc: Exception) -> str:
    raw = str(exc).strip()
    if not raw:
        return "Request failed."
    raw = re.sub(r"https?://\S+", "[url]", raw)
    raw = re.sub(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[redacted]", raw, flags=re.IGNORECASE)
    raw = re.sub(r"([?&](?:key|token|secret|password)=)[^&\s]+", r"\1[redacted]", raw, flags=re.IGNORECASE)
    return raw[:240]


def _source_from_filter(source: str | None) -> str | None:
    if not source or source == "all":
        return None
    if source.startswith("mcp_") or source == "system":
        return source
    return f"mcp_{source}"


def _should_poll_connector(name: str, connector: Any) -> bool:
    config = getattr(connector, "config", {}) or {}
    mode = str(config.get("inbound_mode") or config.get("mode") or "").lower()
    if mode == "webhook" or config.get("polling_enabled") is False:
        return False
    return name not in {"telegram", "whatsapp"}


def _enforce_webhook_defaults(services: AppServices) -> None:
    telegram_config = services.config.setdefault("plugins", {}).setdefault("telegram", {})
    telegram_config.setdefault("inbound_mode", "webhook")
    if telegram_webhook_mode(services.config) == "webhook":
        telegram_config["polling_enabled"] = False
    for name in ("telegram", "whatsapp"):
        connector = services.runner.connectors.get(name)
        if connector is None:
            continue
        connector_config = getattr(connector, "config", {}) or {}
        connector_config.setdefault("inbound_mode", "webhook")
        connector_config["polling_enabled"] = False
        connector.config = connector_config


def _rate_limit_config(config: dict[str, Any]) -> tuple[bool, int, float]:
    rate_config = config.get("rate_limits", {})
    enabled = bool(rate_config.get("enabled", True))
    capacity = int(rate_config.get("expensive_capacity", 20))
    refill = float(rate_config.get("expensive_refill_per_second", capacity / 60))
    return enabled, max(1, capacity), max(0.01, refill)


def _rate_limit_key(request: Request, endpoint: str) -> str:
    client_host = request.client.host if request.client else "unknown"
    return f"{endpoint}:{client_host}"


def _llm_http_exception(exc: LLMUnavailable) -> HTTPException:
    status = 503
    if exc.code == "authentication_failed":
        status = 401
    elif exc.code == "provider_timeout":
        status = 504
    elif exc.code == "rate_limited":
        status = 429
    return HTTPException(status_code=status, detail=exc.message)


def _ensure_llm_ready_for_flow(services_obj: AppServices) -> None:
    # If an LLM provider is explicitly configured, user-facing reasoning flows should
    # report misconfiguration instead of silently falling back forever.
    if services_obj.llm.requires_configuration() and not services_obj.llm.is_configured():
        try:
            services_obj.llm.ensure_available()
        except LLMUnavailable as exc:
            raise _llm_http_exception(exc) from exc


def _startup_validation_errors(services_obj: AppServices) -> list[str]:
    errors: list[str] = []
    env = _environment()
    production = env == "production"

    token = _api_token(services_obj.config)
    token_env = str(services_obj.config.get("api", {}).get("auth_token_env", "SECRET_KEY"))
    if production:
        if not token:
            errors.append(f"{token_env} is required in production for API authentication.")
        elif len(token) < 32:
            errors.append(f"{token_env} must be at least 32 characters in production.")

    llm_error = services_obj.llm.configuration_error
    if llm_error is not None:
        fatal_codes = {"invalid_provider", "missing_base_url", "missing_model", "sdk_missing"}
        if production or llm_error.code in fatal_codes:
            errors.append(llm_error.message)

    telegram_config = services_obj.config.get("plugins", {}).get("telegram", {})
    if telegram_webhook_mode(services_obj.config) == "webhook" and telegram_config.get("polling_enabled") is True:
        errors.append("Telegram polling cannot be enabled while inbound_mode is webhook.")

    return errors


def _validate_startup_config(services_obj: AppServices) -> None:
    errors = _startup_validation_errors(services_obj)
    if errors:
        raise RuntimeError("Startup configuration error: " + " ".join(errors))


# ---------------------------------------------------------------------------
# ChromaDB readiness flag — set after background initialization completes
# ---------------------------------------------------------------------------
_chroma_ready = asyncio.Event()


# ---------------------------------------------------------------------------
# App Factory
# ---------------------------------------------------------------------------
def create_app() -> FastAPI:
    services_obj = build_services()
    _enforce_webhook_defaults(services_obj)
    telegram_secret_state = ensure_telegram_webhook_secret(
        services_obj.config,
        environment=_environment(),
    )
    _validate_startup_config(services_obj)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async def init_chroma():
            await services_obj.vector_store.initialize_async()
            if getattr(services_obj.vector_store, "ready", False):
                _chroma_ready.set()
            else:
                logger.info("ChromaDB is not ready; SQLite/local memory search fallback is active.")

        async def poll_connectors():
            """Poll only connectors that explicitly support polling."""
            seen_ids: set[str] = set()
            # Pre-seed seen_ids from DB so we don't re-alert on startup
            try:
                with services_obj.database.connect() as conn:
                    for row in conn.execute("SELECT id FROM context_events"):
                        seen_ids.add(row[0])
            except Exception:
                pass

            while True:
                await asyncio.sleep(30)
                try:
                    svc = services_obj
                    for source, connector in svc.runner.connectors.items():
                        if not _should_poll_connector(source, connector):
                            continue
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
                                await workflow.ingest_lightweight({"events": [event]})
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

        if _environment() == "production" and not _api_token(services_obj.config):
            logger.warning("SECRET_KEY is not configured; protected API routes will return 503 in production.")
        if telegram_secret_state.generated:
            logger.info("Generated a development Telegram webhook secret. View setup details at /telegram/webhook/setup.")
        elif not telegram_secret_state.configured:
            logger.warning("TELEGRAM_WEBHOOK_SECRET is not configured; Telegram webhook requests will be rejected.")
        logger.info(
            "Telegram webhook setup format: "
            "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<your-ngrok-host>/telegram/webhook"
            "&secret_token=<TELEGRAM_WEBHOOK_SECRET>"
        )
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
        logger.exception("Unhandled API error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    app.state.services = services_obj
    rate_enabled, rate_capacity, rate_refill = _rate_limit_config(services_obj.config)
    app.state.expensive_rate_limit_enabled = rate_enabled
    app.state.expensive_rate_limiter = RateLimiter(capacity=rate_capacity, refill_per_second=rate_refill)

    def services() -> AppServices:
        return app.state.services

    def check_expensive_rate_limit(request: Request, endpoint: str) -> None:
        if not app.state.expensive_rate_limit_enabled:
            return
        limiter: RateLimiter = app.state.expensive_rate_limiter
        if not limiter.allow(_rate_limit_key(request, endpoint)):
            raise HTTPException(status_code=429, detail="Too many requests. Please wait a moment and try again.")

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
        # Dev-mode bypass is only allowed when ENVIRONMENT explicitly opts in.
        token = _api_token(services().config)
        if not token and _is_development():
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
            "llm_status": svc.llm.status().to_dict(),
            "api_auth_configured": bool(_api_token(svc.config)),
            "environment": _environment(),
            "auth_bypass_active": bool(not _api_token(svc.config) and _is_development()),
            "chroma_ready": _chroma_ready.is_set(),
            "telegram_webhook": telegram_webhook_status(svc.config),
        }

    # ── Plugins ────────────────────────────────────────────────────────
    @app.get("/plugins/list")
    def plugins_list() -> dict[str, Any]:
        return {"plugins": services().registry.list_plugins()}

    # ── Events Feed ────────────────────────────────────────────────────
    @app.get("/events/feed")
    def events_feed(
        limit: int = Query(30, ge=1, le=100),
        offset: int = Query(0, ge=0),
        source: str | None = None,
    ) -> dict[str, Any]:
        """Return recent context events for the activity feed."""
        svc = services()
        events, total = svc.database.feed_events(
            limit=limit,
            offset=offset,
            source=_source_from_filter(source),
        )
        serialized = [event.to_dict() for event in events]
        return {
            "events": serialized,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(serialized) < total,
            "next_offset": offset + len(serialized),
        }

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

    @app.post("/events/sync")
    async def events_sync() -> dict[str, Any]:
        """Fetch and store connector data without planning, recommendations, or action routing."""
        svc = services()
        workflow = build_workflow(svc)
        try:
            state = await workflow.ingest_lightweight({})
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Connector sync failed: {_safe_error(exc)}") from exc
        ingested = state.get("events", [])
        for event in ingested:
            await ws_manager.broadcast({"type": "new_event", "payload": event.to_dict()})
        return {
            "ok": True,
            "synced": len(ingested),
            "events": [event.to_dict() for event in ingested],
            "connector_errors": svc.runner.last_fetch_failures,
        }

    @app.post("/events/retrieve")
    def retrieve(request: RetrievalRequest) -> dict[str, Any]:
        svc = services()
        result = svc.retriever.retrieve(request.query, request.limit)
        chroma_ready = bool(getattr(svc.vector_store, "ready", False))
        return {
            "hits": [_retrieval_hit_to_json(hit) for hit in result.hits],
            "structured": result.structured.to_dict(),
            "search_mode": "semantic" if chroma_ready else "keyword_fallback",
            "chroma_ready": chroma_ready,
        }

    # ── Brain Dump (async for LLM calls) ──────────────────────────────
    @app.post("/brain-dump")
    async def brain_dump(request: BrainDumpRequest, raw_request: Request) -> dict[str, Any]:
        check_expensive_rate_limit(raw_request, "brain-dump")
        svc = services()
        _ensure_llm_ready_for_flow(svc)
        workflow = build_workflow(svc)

        event = svc.capture.capture(request.text, title=request.title)
        stored_state = await workflow.ingest({"events": [event]})
        stored_events = stored_state.get("events", [])
        if not stored_events:
            raise HTTPException(status_code=500, detail="Brain dump ingestion produced no stored events")
        stored_event = stored_events[0]

        try:
            intents = await svc.classifier.classify(request.text)
        except LLMUnavailable as exc:
            raise _llm_http_exception(exc) from exc
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
    async def orchestrate(raw_request: Request, request: OrchestrateRequest | None = None) -> dict[str, Any]:
        check_expensive_rate_limit(raw_request, "orchestrate")
        svc = services()
        _ensure_llm_ready_for_flow(svc)
        events = [ContextEvent.from_dict(item) for item in request.events] if request and request.events else None
        workflow = build_workflow(svc)
        try:
            state = await workflow.run(events)
        except LLMUnavailable as exc:
            raise _llm_http_exception(exc) from exc
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
    @app.get("/telegram/webhook/setup")
    def telegram_webhook_setup(public_url: str | None = None) -> dict[str, Any]:
        setup = build_telegram_webhook_setup(services().config, public_url)
        if not setup.get("configured"):
            raise HTTPException(status_code=503, detail="TELEGRAM_WEBHOOK_SECRET is not configured")
        return setup

    @app.post("/telegram/webhook")
    async def telegram_webhook(raw_request: Request) -> dict[str, Any]:
        expected_secret = _telegram_secret(services().config)
        if not expected_secret:
            raise HTTPException(status_code=503, detail="TELEGRAM_WEBHOOK_SECRET is not configured")
        provided_secret = _provided_telegram_secret(raw_request)
        if not provided_secret:
            raise HTTPException(status_code=401, detail="Missing Telegram webhook secret")
        if not compare_digest(provided_secret, expected_secret):
            raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret")
        try:
            body = await raw_request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Malformed Telegram webhook payload") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Malformed Telegram webhook payload")
        update = body.get("update") if isinstance(body.get("update"), dict) else body
        result = await TelegramApprovalBot(services()).handle_update(update)
        # Broadcast webhook event to frontend
        await ws_manager.broadcast({"type": "telegram_webhook", "payload": result})
        if isinstance(result.get("event"), dict):
            await ws_manager.broadcast({"type": "new_event", "payload": result["event"]})
        if result.get("status") in {"approved", "rejected"} and isinstance(result.get("request"), dict):
            await ws_manager.broadcast(
                {
                    "type": "approval_decided",
                    "payload": {"id": result["request"].get("id"), "status": result.get("status")},
                }
            )
        return result

    # ── Live Tasks & Schedule ──────────────────────────────────────────
    @app.get("/tasks/pending")
    def tasks_pending() -> dict[str, Any]:
        """Fetch live pending tasks from Todoist."""
        svc = services()
        connector = svc.runner.connectors.get("todoist")
        if connector is None:
            return {"tasks": [], "source": "disabled", "connected": False, "message": "Todoist connector is disabled."}
        try:
            status = connector.health_status()
            if not status.get("healthy") and status.get("mode") in {"unconfigured", "error"} and not status.get("mock_enabled"):
                return {
                    "tasks": [],
                    "source": str(status.get("mode") or "error"),
                    "connected": False,
                    "error": _safe_error(Exception(str(status.get("error") or "Todoist is not connected."))),
                }
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
            return {"tasks": tasks, "source": "live", "connected": True}
        except Exception as exc:
            return {"tasks": [], "source": "error", "connected": False, "error": _safe_error(exc)}

    @app.get("/schedule/upcoming")
    def schedule_upcoming() -> dict[str, Any]:
        """Fetch live upcoming calendar events."""
        svc = services()
        connector = svc.runner.connectors.get("calendar")
        if connector is None:
            return {
                "events": [],
                "source": "disabled",
                "connected": False,
                "message": "Calendar connector is disabled.",
            }
        try:
            status = connector.health_status()
            if not status.get("healthy") and status.get("mode") in {"unconfigured", "error"} and not status.get("mock_enabled"):
                return {
                    "events": [],
                    "source": str(status.get("mode") or "error"),
                    "connected": False,
                    "error": _safe_error(Exception(str(status.get("error") or "Calendar is not connected."))),
                }
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
            return {"events": schedule, "source": "live", "connected": True}
        except Exception as exc:
            return {"events": [], "source": "error", "connected": False, "error": _safe_error(exc)}

    # ── WhatsApp Cloud API Webhook ──────────────────────────────────────
    @app.get("/whatsapp/webhook")
    async def whatsapp_webhook_verify(request: Request) -> Any:
        """WhatsApp Cloud API sends a GET to verify the webhook URL."""
        params = dict(request.query_params)
        mode = params.get("hub.mode")
        token = params.get("hub.verify_token")
        challenge = params.get("hub.challenge")
        expected = _whatsapp_verify_token(services().config)
        if not expected:
            raise HTTPException(status_code=503, detail="WHATSAPP_VERIFY_TOKEN is not configured")
        if mode == "subscribe" and token == expected:
            return PlainTextResponse(str(challenge or ""))
        return JSONResponse(status_code=403, content={"detail": "Verification failed"})

    @app.post("/whatsapp/webhook")
    async def whatsapp_webhook_receive(request: Request) -> dict[str, Any]:
        """WhatsApp Cloud API POSTs incoming messages here."""
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Malformed WhatsApp webhook payload") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Malformed WhatsApp webhook payload")
        events, ignored = extract_whatsapp_events(body)
        processed = 0
        for event in events:
            original_event_id = event.id
            try:
                svc = services()
                workflow = build_workflow(svc)
                state = await workflow.ingest_lightweight({"events": [event]})
                stored_events = state.get("events", [])
                event = stored_events[0] if stored_events else event
            except Exception as exc:
                logger.warning("WhatsApp webhook ingestion failed for %s: %s", original_event_id, _safe_error(exc))
            await ws_manager.broadcast({"type": "new_event", "payload": event.to_dict()})
            processed += 1
            if "urgent" in event.body.lower():
                await ws_manager.broadcast({
                    "type": "urgent_alert",
                    "payload": {
                        "source": "whatsapp",
                        "title": "Urgent WhatsApp Message",
                        "body": event.body,
                    }
                })
        return {"ok": True, "processed": processed, "ignored": ignored}

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
