from __future__ import annotations

import unittest
from pathlib import Path

from connectors.runner import ConnectorRunner, PluginRegistry


class PluginSystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.registry = PluginRegistry(root_dir=self.root)

    def test_registry_lists_sample_plugins(self) -> None:
        names = {plugin["name"] for plugin in self.registry.list_plugins()}
        self.assertEqual({"gmail", "slack", "whatsapp", "telegram", "calendar", "todoist"}, names)

    def test_runner_loads_enabled_plugins_and_fetches_events(self) -> None:
        runner = ConnectorRunner(self.registry)
        self.assertEqual({"gmail", "slack", "whatsapp", "telegram", "calendar", "todoist"}, set(runner.connectors))
        events = runner.fetch_all_events()
        self.assertIsInstance(events, list)
        self.assertNotIn("telegram", runner.last_fetch_failures)
        self.assertNotIn("whatsapp", runner.last_fetch_failures)
        self.assertTrue(all(event.source.startswith("mcp_") for event in events))


if __name__ == "__main__":
    unittest.main()

