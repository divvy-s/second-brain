from __future__ import annotations

from connectors.base import MCPConnector


class GmailPlugin(MCPConnector):
    name = "gmail"
    version = "1.0.0"
    auth_type = "oauth"

