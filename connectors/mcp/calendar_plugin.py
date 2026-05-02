from __future__ import annotations

from connectors.base import MCPConnector


class CalendarPlugin(MCPConnector):
    name = "calendar"
    version = "1.0.0"
    auth_type = "oauth"
