from __future__ import annotations

from typing import Any

from memory.database import MemoryDatabase


class RollbackManager:
    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database

    def register(self, action_id: str, undo_action: dict[str, Any] | None) -> None:
        if not undo_action:
            return
        self.database.save_rollback_action(action_id, undo_action, used=False)

    def get(self, action_id: str) -> dict[str, Any] | None:
        action = self.database.get_rollback_action(action_id)
        if action and action.get("used"):
            return None
        if action:
            action.pop("used", None)
        return action

    def mark_used(self, action_id: str) -> None:
        self.database.mark_rollback_action_used(action_id)

