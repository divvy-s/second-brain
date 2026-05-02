from __future__ import annotations

from typing import Any

from connectors.base import redact
from memory.database import MemoryDatabase


class AuditLogger:
    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database

    def log(
        self,
        *,
        action_id: str,
        action_type: str,
        plugin: str,
        status: str,
        risk: str,
        request: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        self.database.log_action(
            action_id=action_id,
            action_type=action_type,
            plugin=plugin,
            status=status,
            risk=risk,
            request=redact(request),
            result=redact(result),
        )

