from __future__ import annotations

import uuid
from typing import Any

from connectors.runner import ConnectorRunner
from execution.approval import ApprovalGate
from execution.audit import AuditLogger
from execution.rate_limiter import RateLimiter
from execution.rollback import RollbackManager


class ActionExecutor:
    def __init__(
        self,
        runner: ConnectorRunner,
        approval_gate: ApprovalGate,
        audit_logger: AuditLogger,
        rollback: RollbackManager,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.runner = runner
        self.approval_gate = approval_gate
        self.audit_logger = audit_logger
        self.rollback = rollback
        self.rate_limiter = rate_limiter or RateLimiter()

    def execute(self, action: dict[str, Any]) -> dict[str, Any]:
        action_id = str(action.get("id") or uuid.uuid4())
        action = dict(action)
        action["id"] = action_id
        plugin = str(action.get("plugin") or action.get("connector") or "")
        action_type = str(action.get("type") or "unknown")
        gate = self.approval_gate.evaluate(action)
        risk = str(gate["risk"])
        if gate["status"] == "pending":
            result = {"status": "pending_approval", "approval_request_id": gate["approval_request_id"]}
            self.audit_logger.log(
                action_id=action_id,
                action_type=action_type,
                plugin=plugin,
                status="pending_approval",
                risk=risk,
                request=action,
                result=result,
            )
            return result
        if gate["status"] == "rejected":
            result = {"status": "rejected"}
            self.audit_logger.log(
                action_id=action_id,
                action_type=action_type,
                plugin=plugin,
                status="rejected",
                risk=risk,
                request=action,
                result=result,
            )
            return result
        if not self.rate_limiter.allow(plugin or "default"):
            result = {"status": "rate_limited"}
            self.audit_logger.log(
                action_id=action_id,
                action_type=action_type,
                plugin=plugin,
                status="rate_limited",
                risk=risk,
                request=action,
                result=result,
            )
            return result
        try:
            result = self.runner.execute_action(action)
            self.rollback.register(action_id, action.get("undo_action"))
            status = "executed"
        except Exception as exc:
            result = {"status": "failed", "error": str(exc)}
            status = "failed"
        self.audit_logger.log(
            action_id=action_id,
            action_type=action_type,
            plugin=plugin,
            status=status,
            risk=risk,
            request=action,
            result=result,
        )
        return result | {"status": status, "action_id": action_id}

    def execute_approved(self, request_id: str) -> dict[str, Any]:
        request = self.approval_gate.store.get(request_id)
        if request is None:
            raise KeyError(f"Approval request not found: {request_id}")
        if request.status != "approved":
            return {"status": request.status, "approval_request_id": request_id}
        action = dict(request.action)
        action["approval_request_id"] = request_id
        return self.execute(action)

    def rollback_action(self, action_id: str) -> dict[str, Any]:
        undo = self.rollback.get(action_id)
        if not undo:
            return {"status": "not_rollbackable", "action_id": action_id}
        result = self.execute(undo)
        if result.get("status") == "executed":
            self.rollback.mark_used(action_id)
        return result
