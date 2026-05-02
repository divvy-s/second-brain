from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PluginInstallRequest(BaseModel):
    path: str


class PluginNameRequest(BaseModel):
    name: str


class BrainDumpRequest(BaseModel):
    text: str = Field(min_length=1)
    title: str | None = None


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = 8


class ActionRequest(BaseModel):
    action: dict[str, Any]


class RollbackRequest(BaseModel):
    action_id: str


class ApprovalDecisionRequest(BaseModel):
    request_id: str


class FeedbackRequest(BaseModel):
    event_id: str | None = None
    action_id: str | None = None
    rating: int = Field(ge=-1, le=1)
    note: str = ""


class OrchestrateRequest(BaseModel):
    events: list[dict[str, Any]] | None = None


class TelegramWebhookRequest(BaseModel):
    update: dict[str, Any]

