from __future__ import annotations

from connectors.base import MCPConnector


class WhatsAppPlugin(MCPConnector):
    name = "whatsapp"
    version = "1.0.0"
    auth_type = "api_key"

