from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from connectors.base import redact, utc_now
from memory.database import MemoryDatabase


RISK_ORDER = {"low": 1, "medium": 2, "high": 3}
WRITE_ACTIONS = {
    "send_email",
    "post_message",
    "send_message",
    "create_task",
    "create_calendar_event",
    "delete",
    "mark_read",
}


@dataclass(frozen=True)
class ApprovalRequest:
    id: str
    action: dict[str, Any]
    risk: str
    status: str
    created_at: str
    decided_at: str | None = None


class ApprovalStore:
    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database

    def save(self, request: ApprovalRequest) -> None:
        self.database.save_approval_request(
            request_id=request.id,
            action=request.action,
            risk=request.risk,
            status=request.status,
            created_at=request.created_at,
            decided_at=request.decided_at,
        )

    def get(self, request_id: str) -> ApprovalRequest | None:
        item = self.database.get_approval_request(request_id)
        return ApprovalRequest(**item) if item else None

    def list(self, status: str | None = None) -> list[ApprovalRequest]:
        return [ApprovalRequest(**item) for item in self.database.list_approval_requests(status)]


class RiskPolicy:
    def __init__(self, threshold: str = "medium") -> None:
        self.threshold = threshold if threshold in RISK_ORDER else "medium"

    def normalize_risk(self, action: dict[str, Any]) -> str:
        risk = str(action.get("risk", "")).lower()
        if risk in RISK_ORDER:
            return risk
        action_type = str(action.get("type", "")).lower()
        if action_type.startswith("delete"):
            return "high"
        if action_type in WRITE_ACTIONS:
            return "medium"
        return "low"

    def requires_approval(self, action: dict[str, Any]) -> bool:
        risk = self.normalize_risk(action)
        if str(action.get("force_approval", "")).lower() == "true":
            return True
        return RISK_ORDER[risk] >= RISK_ORDER[self.threshold]


class ApprovalGate:
    def __init__(self, store: ApprovalStore, policy: RiskPolicy | None = None) -> None:
        self.store = store
        self.policy = policy or RiskPolicy()

    def evaluate(self, action: dict[str, Any]) -> dict[str, Any]:
        risk = self.policy.normalize_risk(action)
        approved_request_id = action.get("approval_request_id")
        if approved_request_id:
            existing = self.store.get(str(approved_request_id))
            if existing is None:
                raise KeyError(f"Approval request not found: {approved_request_id}")
            if existing.status == "approved":
                return {"status": "approved", "risk": existing.risk, "auto_approved": False}
            if existing.status == "rejected":
                return {"status": "rejected", "risk": existing.risk}
            return {"status": "pending", "risk": existing.risk, "approval_request_id": existing.id}
        if not self.policy.requires_approval(action):
            return {"status": "approved", "risk": risk, "auto_approved": True}
        request = ApprovalRequest(
            id=str(uuid.uuid4()),
            action=redact(action),
            risk=risk,
            status="pending",
            created_at=utc_now().isoformat(),
        )
        self.store.save(request)
        return {"status": "pending", "risk": risk, "approval_request_id": request.id}

    def approve(self, request_id: str) -> ApprovalRequest:
        request = self.store.get(request_id)
        if request is None:
            raise KeyError(f"Approval request not found: {request_id}")
        approved = ApprovalRequest(
            id=request.id,
            action=request.action,
            risk=request.risk,
            status="approved",
            created_at=request.created_at,
            decided_at=utc_now().isoformat(),
        )
        self.store.save(approved)
        return approved

    def reject(self, request_id: str) -> ApprovalRequest:
        request = self.store.get(request_id)
        if request is None:
            raise KeyError(f"Approval request not found: {request_id}")
        rejected = ApprovalRequest(
            id=request.id,
            action=request.action,
            risk=request.risk,
            status="rejected",
            created_at=request.created_at,
            decided_at=utc_now().isoformat(),
        )
        self.store.save(rejected)
        return rejected
