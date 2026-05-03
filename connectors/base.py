from __future__ import annotations

import json
import subprocess
import sys
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar


SECRET_MARKERS = ("key", "token", "secret", "password", "credential")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware(value: datetime | str | None) -> datetime:
    if value is None:
        return utc_now()
    if isinstance(value, str):
        cleaned = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(cleaned)
    else:
        parsed = value
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if any(marker in key.lower() for marker in SECRET_MARKERS):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact(item)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


@dataclass(slots=True)
class ContextEvent:
    source: str
    kind: str
    title: str
    body: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: datetime = field(default_factory=utc_now)
    participants: list[str] = field(default_factory=list)
    importance: float = 0.5
    entities: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.occurred_at = ensure_aware(self.occurred_at)
        self.importance = max(0.0, min(1.0, float(self.importance)))
        if not self.source.startswith("mcp_") and self.source != "system":
            self.source = f"mcp_{self.source}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["occurred_at"] = self.occurred_at.isoformat()
        data["metadata"] = redact(data.get("metadata", {}))
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextEvent":
        return cls(
            id=str(data.get("id") or uuid.uuid4()),
            source=str(data["source"]),
            kind=str(data.get("kind", "event")),
            title=str(data.get("title", "")),
            body=str(data.get("body", "")),
            occurred_at=ensure_aware(data.get("occurred_at")),
            participants=list(data.get("participants") or []),
            importance=float(data.get("importance", 0.5)),
            entities=list(data.get("entities") or []),
            metadata=dict(data.get("metadata") or {}),
        )


class BaseConnector(ABC):
    name: ClassVar[str]
    version: ClassVar[str]
    auth_type: ClassVar[str]

    def __init__(self, config: dict[str, Any] | None = None, root_dir: Path | None = None) -> None:
        self.config = config or {}
        self.root_dir = root_dir or Path(__file__).resolve().parents[1]

    @abstractmethod
    def authenticate(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def fetch_events(self) -> list[ContextEvent]:
        raise NotImplementedError

    @abstractmethod
    def execute_action(self, action: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> bool:
        raise NotImplementedError

    def health_status(self) -> dict[str, Any]:
        return {"healthy": self.health_check()}


class MCPConnector(BaseConnector):
    cli_timeout_seconds: ClassVar[int] = 30

    @property
    def source(self) -> str:
        return f"mcp_{self.name}"

    def make_event(
        self,
        *,
        kind: str,
        title: str,
        body: str,
        event_id: str | None = None,
        occurred_at: datetime | str | None = None,
        participants: list[str] | None = None,
        importance: float = 0.5,
        entities: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ContextEvent:
        return ContextEvent(
            id=event_id or str(uuid.uuid4()),
            source=self.source,
            kind=kind,
            title=title,
            body=body,
            occurred_at=ensure_aware(occurred_at),
            participants=participants or [],
            importance=importance,
            entities=entities or [],
            metadata=metadata or {},
        )

    def run_cli(
        self,
        operation: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        cli = self.root_dir / "scripts" / "external_cli.py"
        request = {
            "config": self.config,
            "payload": payload or {},
        }
        completed = subprocess.run(
            [sys.executable, str(cli), self.name, operation],
            input=json.dumps(request),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds or self.cli_timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "CLI wrapper failed"
            raise RuntimeError(f"{self.name}.{operation} failed: {detail}")
        try:
            response = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{self.name}.{operation} returned invalid JSON") from exc
        if not response.get("ok", False):
            safe_response = redact(response)
            raise RuntimeError(f"{self.name}.{operation} rejected request: {safe_response}")
        return response

    def authenticate(self) -> bool:
        return self.health_check()

    def fetch_events(self) -> list[ContextEvent]:
        response = self.run_cli("fetch_events")
        events: list[ContextEvent] = []
        for item in response.get("events", []):
            payload = dict(item)
            payload.setdefault("source", self.source)
            events.append(ContextEvent.from_dict(payload))
        return events

    def execute_action(self, action: dict[str, Any]) -> dict[str, Any]:
        return redact(self.run_cli("execute_action", action))

    def health_check(self) -> bool:
        return bool(self.health_status().get("healthy", False))

    def health_status(self, timeout_seconds: int | None = None) -> dict[str, Any]:
        return redact(self.run_cli("health_check", timeout_seconds=timeout_seconds))

