from __future__ import annotations

from typing import Any

from memory.database import MemoryDatabase


class RollbackManager:
    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database

    def register(self, action_id: str, undo_action: dict[str, Any] | None) -> None:
        if not undo_action:
            return
        actions = self.database.get_setting("rollback_actions", {})
        actions[action_id] = undo_action
        self.database.set_setting("rollback_actions", actions)

    def get(self, action_id: str) -> dict[str, Any] | None:
        return self.database.get_setting("rollback_actions", {}).get(action_id)

    def mark_used(self, action_id: str) -> None:
        actions = self.database.get_setting("rollback_actions", {})
        if action_id in actions:
            actions[action_id]["used"] = True
            self.database.set_setting("rollback_actions", actions)

