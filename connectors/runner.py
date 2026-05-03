from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from connectors.base import BaseConnector, ContextEvent, MCPConnector, redact
from connectors.config import dump_config, load_config, update_yaml_scalar


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PluginDescriptor:
    name: str
    module: str
    class_name: str
    version: str
    auth_type: str
    location: str = "built_in"


class PluginLoadError(RuntimeError):
    pass


class PluginRegistry:
    def __init__(
        self,
        root_dir: Path | None = None,
        registry_path: Path | None = None,
        config_path: Path | None = None,
    ) -> None:
        self.root_dir = root_dir or Path(__file__).resolve().parents[1]
        self.registry_path = registry_path or self.root_dir / "connectors" / "mcp" / "plugin_registry.json"
        self.config_path = config_path or self.root_dir / "config" / "user_config.yml"
        self.user_plugin_dir = self.root_dir / "user_plugins"

    def read_registry(self) -> list[PluginDescriptor]:
        data = json.loads(self.registry_path.read_text(encoding="utf-8"))
        descriptors = [
            PluginDescriptor(
                name=item["name"],
                module=item["module"],
                class_name=item["class"],
                version=item.get("version", "0.0.0"),
                auth_type=item.get("auth_type", "none"),
                location=item.get("location", "built_in"),
            )
            for item in data.get("plugins", [])
        ]
        descriptors.extend(self._discover_user_plugins())
        return descriptors

    def _discover_user_plugins(self) -> list[PluginDescriptor]:
        descriptors: list[PluginDescriptor] = []
        self.user_plugin_dir.mkdir(parents=True, exist_ok=True)
        for file_path in sorted(self.user_plugin_dir.glob("*.py")):
            if file_path.name.startswith("_"):
                continue
            module = self._load_module_from_file(file_path)
            for attr in dir(module):
                value = getattr(module, attr)
                if (
                    isinstance(value, type)
                    and issubclass(value, MCPConnector)
                    and value is not MCPConnector
                    and getattr(value, "name", None)
                ):
                    descriptors.append(
                        PluginDescriptor(
                            name=value.name,
                            module=f"user_plugins.{file_path.stem}",
                            class_name=value.__name__,
                            version=getattr(value, "version", "0.0.0"),
                            auth_type=getattr(value, "auth_type", "none"),
                            location="user",
                        )
                    )
        return descriptors

    def _load_module_from_file(self, file_path: Path) -> ModuleType:
        module_name = f"user_plugins.{file_path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            raise PluginLoadError(f"Could not load user plugin from {file_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def load_connector_class(self, descriptor: PluginDescriptor) -> type[BaseConnector]:
        if descriptor.location == "user":
            module_path = self.user_plugin_dir / f"{descriptor.module.rsplit('.', 1)[-1]}.py"
            module = self._load_module_from_file(module_path)
        else:
            module = importlib.import_module(descriptor.module)
        connector_cls = getattr(module, descriptor.class_name, None)
        if not isinstance(connector_cls, type) or not issubclass(connector_cls, BaseConnector):
            raise PluginLoadError(f"{descriptor.name} does not implement BaseConnector")
        self._validate_connector_class(connector_cls)
        return connector_cls

    def _validate_connector_class(self, connector_cls: type[BaseConnector]) -> None:
        required = ("authenticate", "fetch_events", "execute_action", "health_check")
        missing = [name for name in required if not callable(getattr(connector_cls, name, None))]
        if missing:
            raise PluginLoadError(f"{connector_cls.__name__} is missing {', '.join(missing)}")

    def load_enabled_plugins(self) -> dict[str, BaseConnector]:
        config = load_config(self.config_path)
        plugin_config = config.get("plugins", {})
        connectors: dict[str, BaseConnector] = {}
        for descriptor in self.read_registry():
            settings = dict(plugin_config.get(descriptor.name, {}))
            if not settings.get("enabled", False):
                continue
            connector_cls = self.load_connector_class(descriptor)
            connectors[descriptor.name] = connector_cls(config=settings, root_dir=self.root_dir)
        return connectors

    def list_plugins(self) -> list[dict[str, Any]]:
        config = load_config(self.config_path)
        enabled_config = config.get("plugins", {})
        loaded: list[dict[str, Any]] = []
        for descriptor in self.read_registry():
            settings = enabled_config.get(descriptor.name, {})
            loaded.append(
                {
                    "name": descriptor.name,
                    "version": descriptor.version,
                    "auth_type": descriptor.auth_type,
                    "location": descriptor.location,
                    "enabled": bool(settings.get("enabled", False)),
                    "config": redact(settings),
                }
            )
        return loaded

    def install_plugin(self, source_path: str | Path) -> dict[str, Any]:
        source = Path(source_path)
        if not source.exists():
            raise FileNotFoundError(f"Plugin source does not exist: {source}")
        self.user_plugin_dir.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            candidates = list(source.glob("*.py"))
            if len(candidates) != 1:
                raise PluginLoadError("Install directory must contain exactly one Python plugin file")
            source = candidates[0]
        target = self.user_plugin_dir / source.name
        shutil.copy2(source, target)
        descriptors = self._discover_user_plugins()
        installed = [item for item in descriptors if item.module.endswith(target.stem)]
        if not installed:
            target.unlink(missing_ok=True)
            raise PluginLoadError("Installed file did not expose an MCPConnector subclass")
        return {"installed": [item.name for item in installed], "path": str(target)}

    def set_plugin_enabled(self, plugin_name: str, enabled: bool) -> dict[str, Any]:
        config = load_config(self.config_path)
        config.setdefault("plugins", {}).setdefault(plugin_name, {})["enabled"] = enabled
        if not update_yaml_scalar(self.config_path, ["plugins", plugin_name, "enabled"], enabled):
            dump_config(self.config_path, config)
        return {"name": plugin_name, "enabled": enabled}


class ConnectorRunner:
    def __init__(self, registry: PluginRegistry | None = None) -> None:
        self.registry = registry or PluginRegistry()
        self.connectors = self.registry.load_enabled_plugins()
        self.last_fetch_failures: dict[str, str] = {}

    def refresh(self) -> None:
        self.connectors = self.registry.load_enabled_plugins()
        self.last_fetch_failures = {}

    def _polling_disabled_reason(self, name: str, connector: BaseConnector) -> str | None:
        mode = str(connector.config.get("inbound_mode") or connector.config.get("mode") or "").lower()
        if name in {"telegram", "whatsapp"} and mode in {"", "webhook"}:
            return "webhook_only"
        if mode == "webhook":
            return "webhook_only"
        if connector.config.get("polling_enabled") is False:
            return "polling_disabled"
        return None

    def fetch_all_events(self) -> list[ContextEvent]:
        events: list[ContextEvent] = []
        failures: dict[str, str] = {}
        for name, connector in self.connectors.items():
            disabled_reason = self._polling_disabled_reason(name, connector)
            if disabled_reason:
                logger.info("Skipping %s connector fetch because %s", name, disabled_reason)
                continue
            try:
                events.extend(connector.fetch_events())
            except Exception as exc:
                failures[name] = str(exc)
                logger.warning("Connector fetch failed for %s: %s", name, exc)
        self.last_fetch_failures = failures
        if failures and not events:
            raise RuntimeError(f"All connectors failed: {redact(failures)}")
        return events

    def execute_action(self, action: dict[str, Any]) -> dict[str, Any]:
        plugin_name = action.get("plugin") or action.get("connector") or action.get("source", "").removeprefix("mcp_")
        if not plugin_name:
            raise ValueError("Action must include plugin, connector, or source")
        connector = self.connectors.get(str(plugin_name))
        if connector is None:
            raise KeyError(f"Connector is not enabled: {plugin_name}")
        return connector.execute_action(action)

    def health(self) -> dict[str, dict[str, Any]]:
        status: dict[str, dict[str, Any]] = {}
        for name, connector in self.connectors.items():
            try:
                details = connector.health_status()
                details.setdefault("healthy", bool(details.get("healthy", False)))
                if name in self.last_fetch_failures:
                    details["last_fetch_error"] = self.last_fetch_failures[name]
                status[name] = details
            except Exception as exc:
                status[name] = {"healthy": False, "error": str(exc)}
        return status

