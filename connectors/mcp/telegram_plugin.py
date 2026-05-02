from __future__ import annotations

from connectors.base import MCPConnector


class TelegramPlugin(MCPConnector):
    name = "telegram"
    version = "1.0.0"
    auth_type = "api_key"

