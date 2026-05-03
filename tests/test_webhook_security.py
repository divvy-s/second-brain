from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from api.webhook_security import (
    build_telegram_webhook_setup,
    ensure_telegram_webhook_secret,
    read_telegram_webhook_secret,
    telegram_webhook_status,
)


class TelegramWebhookSecurityTests(unittest.TestCase):
    def test_env_secret_wins_without_mutating_config_secret(self) -> None:
        config = {"plugins": {"telegram": {"webhook_secret_env": "TELEGRAM_WEBHOOK_SECRET"}}}
        with patch.dict(os.environ, {"TELEGRAM_WEBHOOK_SECRET": "env-secret"}, clear=False):
            resolved = read_telegram_webhook_secret(config)
        self.assertEqual(resolved.secret, "env-secret")
        self.assertEqual(resolved.source, "env")
        self.assertNotIn("webhook_secret", config["plugins"]["telegram"])

    def test_development_generates_runtime_secret_when_missing(self) -> None:
        config = {"plugins": {"telegram": {"webhook_secret_env": "TELEGRAM_WEBHOOK_SECRET"}}}
        with patch.dict(os.environ, {}, clear=True):
            resolved = ensure_telegram_webhook_secret(
                config,
                environment="development",
                generator=lambda size: f"generated-{size}",
            )
        self.assertEqual(resolved.secret, "generated-32")
        self.assertEqual(resolved.source, "generated")
        self.assertEqual(config["plugins"]["telegram"]["webhook_secret"], "generated-32")

    def test_production_missing_secret_stays_unconfigured(self) -> None:
        config = {"plugins": {"telegram": {"webhook_secret_env": "TELEGRAM_WEBHOOK_SECRET"}}}
        with patch.dict(os.environ, {}, clear=True):
            resolved = ensure_telegram_webhook_secret(config, environment="production")
        self.assertFalse(resolved.configured)
        self.assertEqual(telegram_webhook_status(config)["safe_for_webhook"], False)

    def test_setup_output_builds_ngrok_set_webhook_url(self) -> None:
        config = {"plugins": {"telegram": {"webhook_secret": "telegram-secret"}}}
        setup = build_telegram_webhook_setup(config, "https://abc123.ngrok-free.app")
        self.assertTrue(setup["configured"])
        self.assertEqual(setup["webhook_url"], "https://abc123.ngrok-free.app/telegram/webhook")
        self.assertIn("https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook?", setup["set_webhook_url"])
        self.assertIn("secret_token=telegram-secret", setup["set_webhook_url"])
        self.assertEqual(
            setup["set_webhook_url_display"],
            "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook?url=https://abc123.ngrok-free.app/telegram/webhook&secret_token=telegram-secret",
        )


if __name__ == "__main__":
    unittest.main()
