from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
RISK_BASE_SCORES = {
    "draft_email": 0.24,
    "mark_read": 0.18,
    "create_task": 0.34,
    "create_calendar_draft": 0.36,
    "create_calendar_event": 0.52,
    "create_event": 0.52,
    "send_message": 0.66,
    "post_message": 0.68,
    "send_email": 0.74,
}


@dataclass(frozen=True)
class RiskAssessment:
    level: str
    score: float
    reasons: list[str]
    requires_approval: bool


@dataclass(frozen=True)
class ApprovalRequest:
    id: str
    action: dict[str, Any]
    risk: str
    status: str
    created_at: str
    decided_at: str | None = None
    risk_score: float = 0.0
    risk_reasons: list[str] = field(default_factory=list)


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

    def _inflate_request(self, item: dict[str, Any]) -> ApprovalRequest:
        action = dict(item.get("action") or {})
        risk_score = float(action.pop("_risk_score", action.pop("risk_score", 0.0)) or 0.0)
        risk_reasons = list(action.pop("_risk_reasons", action.pop("risk_reasons", [])) or [])
        return ApprovalRequest(
            id=str(item["id"]),
            action=action,
            risk=str(item["risk"]),
            status=str(item["status"]),
            created_at=str(item["created_at"]),
            decided_at=item.get("decided_at"),
            risk_score=risk_score,
            risk_reasons=risk_reasons,
        )

    def get(self, request_id: str) -> ApprovalRequest | None:
        item = self.database.get_approval_request(request_id)
        return self._inflate_request(item) if item else None

    def list(self, status: str | None = None) -> list[ApprovalRequest]:
        return [self._inflate_request(item) for item in self.database.list_approval_requests(status)]


class RiskPolicy:
    def __init__(self, threshold: str = "medium") -> None:
        self.threshold = threshold if threshold in RISK_ORDER else "medium"

    def assess(self, action: dict[str, Any]) -> RiskAssessment:
        """Return a numeric risk score plus the approval decision inputs."""
        action_type = str(action.get("type", "")).lower()
        plugin = str(action.get("plugin") or action.get("connector") or "").lower()
        force_approval = str(action.get("force_approval", "")).strip().lower() in {"1", "true", "yes", "on"}
        score = float(RISK_BASE_SCORES.get(action_type, 0.42))
        reasons = [f"base:{action_type or 'unknown'}={score:.2f}"]

        if plugin in {"gmail", "telegram", "whatsapp", "slack"} and action_type not in {"draft_email", "mark_read"}:
            score += 0.05
            reasons.append(f"plugin:{plugin}")
        if any(action.get(key) for key in ("to", "recipient", "chat_id", "channel")):
            score += 0.08
            reasons.append("external_recipient")
        if action_type.startswith("delete"):
            score += 0.18
            reasons.append("destructive_action")
        if action_type in WRITE_ACTIONS and not action.get("undo_action"):
            score += 0.05
            reasons.append("no_rollback")

        explicit_level = str(action.get("risk", "")).lower()
        if explicit_level in RISK_ORDER:
            score = max(score, {"low": 0.22, "medium": 0.55, "high": 0.85}[explicit_level])
            reasons.append(f"declared:{explicit_level}")

        normalized_score = max(0.0, min(1.0, score))
        derived_level = self._level_for_score(normalized_score)
        level = self._max_level(explicit_level, derived_level) if explicit_level in RISK_ORDER else derived_level
        requires_approval = force_approval or RISK_ORDER[level] >= RISK_ORDER[self.threshold]
        if force_approval:
            reasons.append("force_approval")
        return RiskAssessment(
            level=level,
            score=normalized_score,
            reasons=reasons,
            requires_approval=requires_approval,
        )

    def normalize_risk(self, action: dict[str, Any]) -> str:
        return self.assess(action).level

    def requires_approval(self, action: dict[str, Any]) -> bool:
        return self.assess(action).requires_approval

    def _level_for_score(self, score: float) -> str:
        if score >= 0.75:
            return "high"
        if score >= 0.4:
            return "medium"
        return "low"

    def _max_level(self, left: str, right: str) -> str:
        if left not in RISK_ORDER:
            return right
        return left if RISK_ORDER[left] >= RISK_ORDER[right] else right


class ApprovalGate:
    def __init__(self, store: ApprovalStore, policy: RiskPolicy | None = None) -> None:
        self.store = store
        self.policy = policy or RiskPolicy()

    def evaluate(self, action: dict[str, Any]) -> dict[str, Any]:
        assessment = self.policy.assess(action)
        risk = assessment.level
        approved_request_id = action.get("approval_request_id")
        if approved_request_id:
            existing = self.store.get(str(approved_request_id))
            if existing is None:
                raise KeyError(f"Approval request not found: {approved_request_id}")
            if existing.status == "approved":
                return {
                    "status": "approved",
                    "risk": existing.risk,
                    "risk_score": existing.risk_score,
                    "risk_reasons": existing.risk_reasons,
                    "auto_approved": False,
                }
            if existing.status == "rejected":
                return {
                    "status": "rejected",
                    "risk": existing.risk,
                    "risk_score": existing.risk_score,
                    "risk_reasons": existing.risk_reasons,
                }
            if existing.status == "ignored":
                return {
                    "status": "ignored",
                    "risk": existing.risk,
                    "risk_score": existing.risk_score,
                    "risk_reasons": existing.risk_reasons,
                }
            return {
                "status": "pending",
                "risk": existing.risk,
                "risk_score": existing.risk_score,
                "risk_reasons": existing.risk_reasons,
                "approval_request_id": existing.id,
            }
        if not assessment.requires_approval:
            return {
                "status": "approved",
                "risk": risk,
                "risk_score": assessment.score,
                "risk_reasons": assessment.reasons,
                "auto_approved": True,
            }
        stored_action = dict(redact(action))
        stored_action["_risk_score"] = assessment.score
        stored_action["_risk_reasons"] = assessment.reasons
        request = ApprovalRequest(
            id=str(uuid.uuid4()),
            action=stored_action,
            risk=risk,
            status="pending",
            created_at=utc_now().isoformat(),
            risk_score=assessment.score,
            risk_reasons=assessment.reasons,
        )
        self.store.save(request)
        return {
            "status": "pending",
            "risk": risk,
            "risk_score": assessment.score,
            "risk_reasons": assessment.reasons,
            "approval_request_id": request.id,
        }

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
            risk_score=request.risk_score,
            risk_reasons=request.risk_reasons,
        )
        self.store.save(approved)
        self._apply_feedback(approved, rating=1, note="approval_accepted")
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
            risk_score=request.risk_score,
            risk_reasons=request.risk_reasons,
        )
        self.store.save(rejected)
        self._apply_feedback(rejected, rating=-1, note="approval_rejected")
        return rejected

    def ignore(self, request_id: str, note: str = "approval_ignored") -> ApprovalRequest:
        """Mark a stale approval request as ignored and reduce its future rank."""
        request = self.store.get(request_id)
        if request is None:
            raise KeyError(f"Approval request not found: {request_id}")
        ignored = ApprovalRequest(
            id=request.id,
            action=request.action,
            risk=request.risk,
            status="ignored",
            created_at=request.created_at,
            decided_at=utc_now().isoformat(),
            risk_score=request.risk_score,
            risk_reasons=request.risk_reasons,
        )
        self.store.save(ignored)
        self._apply_feedback(ignored, rating=-1, note=note)
        return ignored

    def ignore_stale(self, max_age_minutes: float) -> list[ApprovalRequest]:
        """Expire old pending requests so ignored actions feed the ranking loop."""
        if max_age_minutes <= 0:
            return []
        cutoff_seconds = float(max_age_minutes) * 60.0
        ignored: list[ApprovalRequest] = []
        now = datetime.now(timezone.utc)
        for request in self.store.list("pending"):
            created_at = datetime.fromisoformat(request.created_at.replace("Z", "+00:00"))
            age_seconds = (now - created_at).total_seconds()
            if age_seconds >= cutoff_seconds:
                ignored.append(self.ignore(request.id, note="approval_stale"))
        return ignored

    def _apply_feedback(self, request: ApprovalRequest, *, rating: int, note: str) -> None:
        source_event_id = str(request.action.get("source_event_id") or "").strip()
        self.store.database.record_feedback(
            event_id=source_event_id or None,
            action_id=None,
            rating=rating,
            note=f"{note}:{request.id}",
        )
        if not source_event_id:
            return
        if rating > 0:
            self.store.database.adjust_event_importance(source_event_id, 0.04)
            self.store.database.adjust_event_reinforcement(source_event_id, 0.08)
            self.store.database.increment_event_access(source_event_id, reason="planning")
            return
        self.store.database.adjust_event_importance(source_event_id, -0.08)
        self.store.database.adjust_event_reinforcement(source_event_id, -0.14)
