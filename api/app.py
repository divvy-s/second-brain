from __future__ import annotations

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


def _state_to_json(state: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in state.items():
        if key == "events":
            output[key] = [event.to_dict() for event in value]
        elif key == "prioritized":
            output[key] = [{"event": event.to_dict(), "score": score} for event, score in value]
        else:
            output[key] = value
    return output


def create_app() -> FastAPI:
    app = FastAPI(title="Second Brain", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
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
    app.state.services = build_services()

    def services() -> AppServices:
        return app.state.services

    @app.get("/health")
    def health() -> dict[str, Any]:
        svc = services()
        return {
            "ok": True,
            "plugins": svc.runner.health(),
            "redis_backed": svc.event_bus.is_redis_backed,
            "llm_configured": svc.llm.is_configured(),
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
        for event in state.get("events", []):
            svc.event_bus.publish(event)
        return {"events": [event.to_dict() for event in state.get("events", [])]}

    @app.post("/events/retrieve")
    def retrieve(request: RetrievalRequest) -> dict[str, Any]:
        hits = services().retriever.retrieve(request.query, request.limit)
        return {
            "hits": [
                {
                    "event": hit.event.to_dict(),
                    "score": hit.score,
                    "vector_score": hit.vector_score,
                    "keyword_score": hit.keyword_score,
                }
                for hit in hits
            ]
        }

    @app.post("/brain-dump")
    def brain_dump(request: BrainDumpRequest) -> dict[str, Any]:
        svc = services()

        # 1. Capture and store the event
        event = svc.capture.capture(request.text, title=request.title)
        svc.database.add_event(event)
        svc.vector_store.index_event(event)
        svc.event_bus.publish(event)

        # 2. Use the AI Intent Classifier to understand what the user wants
        intents = svc.classifier.classify(request.text)
        
        # 3. Submit each action to the Approval Gate (creates pending requests)
        intent_responses = []
        for intent in intents:
            action = svc.classifier.to_action(intent, source_event_id=event.id)
            svc.approval_gate.evaluate(action)
            intent_responses.append({
                "type": intent.intent,
                "plugin": intent.plugin,
                "confidence": intent.confidence,
                "fields": intent.fields,
                "reasoning": intent.reasoning,
            })

        # 4. Return everything — event, classified intents, and approval status
        pending = [
            r.__dict__ for r in svc.approval_gate.store.list("pending")
            if str(r.action.get("source_event_id")) == event.id
        ]
        return {
            "event": event.to_dict(),
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
        return {"approvals": [request.__dict__ for request in services().approval_gate.store.list(status)]}

    @app.post("/approvals/approve")
    def approvals_approve(request: ApprovalDecisionRequest) -> dict[str, Any]:
        try:
            approved = services().approval_gate.approve(request.request_id)
            execution = services().executor.execute_approved(approved.id)
            return {"request": approved.__dict__, "execution": execution}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/approvals/reject")
    def approvals_reject(request: ApprovalDecisionRequest) -> dict[str, Any]:
        try:
            rejected = services().approval_gate.reject(request.request_id)
            return {"request": rejected.__dict__}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/telegram/webhook")
    def telegram_webhook(request: TelegramWebhookRequest) -> dict[str, Any]:
        return TelegramApprovalBot(services()).handle_update(request.update)

    return app


app = create_app()
