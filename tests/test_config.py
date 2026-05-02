from __future__ import annotations

import unittest
from pathlib import Path

from connectors.config import load_config


class ConfigTests(unittest.TestCase):
    def test_yaml_fallback_loads_default_config(self) -> None:
        root = Path(__file__).resolve().parents[1]
        config = load_config(root / "config" / "user_config.yml")
        self.assertIn("plugins", config)
        self.assertTrue(config["plugins"]["gmail"]["enabled"])
        self.assertEqual(config["plugins"]["gmail"]["access_token_env"], "GMAIL_ACCESS_TOKEN")
        self.assertEqual(config["plugins"]["gmail"]["max_results"], 10)
        self.assertEqual(config["plugins"]["slack"]["channel_ids"], [])


if __name__ == "__main__":
    unittest.main()
