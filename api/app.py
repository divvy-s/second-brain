from __future__ import annotations

import os
from dataclasses import asdict
from secrets import compare_digest
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.dependencies import AppServices, build_services, build_workflow
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


def create_app() -> FastAPI:
    services_obj = build_services()
    app = FastAPI(title="Second Brain", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins(services_obj.config),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        public_paths = {"/health", "/docs", "/openapi.json", "/redoc", "/telegram/webhook"}
        if request.method.upper() == "OPTIONS" or request.url.path in public_paths:
            return await call_next(request)
        token = _api_token(services().config)
        if not token:
            return JSONResponse(status_code=503, content={"detail": "API authentication is not configured"})
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"detail": "Missing Bearer token"})
        provided = authorization.removeprefix("Bearer ").strip()
        if not compare_digest(provided, token):
            return JSONResponse(status_code=403, content={"detail": "Invalid API token"})
        return await call_next(request)

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
        }

    @app.get("/plugins/list")
    def plugins_list() -> dict[str, Any]:
        return {"plugins": services().registry.list_plugins()}

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

    @app.post("/events/ingest")
    def ingest(request: OrchestrateRequest | None = None) -> dict[str, Any]:
        svc = services()
        workflow = build_workflow(svc)
        events = [ContextEvent.from_dict(item) for item in request.events] if request and request.events else None
        state = workflow.ingest({"events": events or []}) if events is not None else workflow.ingest({})
        return {"events": [event.to_dict() for event in state.get("events", [])]}

    @app.post("/events/retrieve")
    def retrieve(request: RetrievalRequest) -> dict[str, Any]:
        result = services().retriever.retrieve(request.query, request.limit)
        return {
            "hits": [_retrieval_hit_to_json(hit) for hit in result.hits],
            "structured": result.structured.to_dict(),
        }

    @app.post("/brain-dump")
    def brain_dump(request: BrainDumpRequest) -> dict[str, Any]:
        svc = services()
        workflow = build_workflow(svc)

        event = svc.capture.capture(request.text, title=request.title)
        stored_state = workflow.ingest({"events": [event]})
        stored_events = stored_state.get("events", [])
        if not stored_events:
            raise HTTPException(status_code=500, detail="Brain dump ingestion produced no stored events")
        stored_event = stored_events[0]

        intents = svc.classifier.classify(request.text)
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
        return {
            "event": stored_event.to_dict(),
            "intents": intent_responses,
            "approvals": pending,
        }

    @app.post("/orchestrate")
    def orchestrate(request: OrchestrateRequest | None = None) -> dict[str, Any]:
        events = [ContextEvent.from_dict(item) for item in request.events] if request and request.events else None
        state = build_workflow(services()).run(events)
        return _state_to_json(state)

    @app.post("/actions/execute")
    def execute_action(request: ActionRequest) -> dict[str, Any]:
        return services().executor.execute(request.action)

    @app.get("/approvals/list")
    def approvals_list(status: str | None = None) -> dict[str, Any]:
        return {"approvals": [asdict(request_item) for request_item in services().approval_gate.store.list(status)]}

    @app.post("/approvals/approve")
    def approvals_approve(request: ApprovalDecisionRequest) -> dict[str, Any]:
        try:
            approved = services().approval_gate.approve(request.request_id)
            execution = services().executor.execute_approved(approved.id)
            return {"request": asdict(approved), "execution": execution}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/approvals/reject")
    def approvals_reject(request: ApprovalDecisionRequest) -> dict[str, Any]:
        try:
            rejected = services().approval_gate.reject(request.request_id)
            return {"request": asdict(rejected)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/telegram/webhook")
    def telegram_webhook(raw_request: Request, request: TelegramWebhookRequest) -> dict[str, Any]:
        expected_secret = _telegram_secret(services().config)
        if expected_secret:
            provided_secret = raw_request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if not compare_digest(provided_secret, expected_secret):
                raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret")
        return TelegramApprovalBot(services()).handle_update(request.update)

    return app


app = create_app()
