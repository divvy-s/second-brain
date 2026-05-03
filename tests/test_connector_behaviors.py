from __future__ import annotations

import unittest
from unittest.mock import patch

from connectors.base import BaseConnector, ContextEvent
from connectors.runner import ConnectorRunner
from scripts.external_cli import (
    google_http_json,
    health,
    is_todoist_onboarding_task,
    slack_fetch,
    telegram_fetch,
    whatsapp_fetch,
)


class PollingConnector(BaseConnector):
    name = "polling"
    version = "1"
    auth_type = "none"

    def __init__(self, config=None, root_dir=None) -> None:
        super().__init__(config=config, root_dir=root_dir)
        self.called = False

    def authenticate(self) -> bool:
        return True

    def fetch_events(self) -> list[ContextEvent]:
        self.called = True
        return [ContextEvent(source=self.name, kind="message", title="ok", body="")]

    def execute_action(self, action):
        return {"ok": True}

    def health_check(self) -> bool:
        return True


class ConnectorBehaviorTests(unittest.TestCase):
    def test_runner_skips_webhook_only_connectors(self) -> None:
        webhook = PollingConnector(config={"inbound_mode": "webhook"})
        polling = PollingConnector(config={})
        runner = ConnectorRunner.__new__(ConnectorRunner)
        runner.connectors = {"telegram": webhook, "slack": polling}
        runner.last_fetch_failures = {}
        events = runner.fetch_all_events()
        self.assertEqual(len(events), 1)
        self.assertFalse(webhook.called)
        self.assertTrue(polling.called)

    def test_runner_treats_telegram_default_as_webhook_only(self) -> None:
        telegram = PollingConnector(config={})
        runner = ConnectorRunner.__new__(ConnectorRunner)
        runner.connectors = {"telegram": telegram}
        runner.last_fetch_failures = {}
        self.assertEqual(runner.fetch_all_events(), [])
        self.assertFalse(telegram.called)

    def test_telegram_and_whatsapp_fetch_are_webhook_safe(self) -> None:
        def fail_http(*args, **kwargs):
            raise AssertionError("webhook mode must not poll provider APIs")

        with patch("scripts.external_cli.http_json", fail_http):
            self.assertEqual(telegram_fetch({"bot_token": "x", "inbound_mode": "webhook"})["events"], [])
            self.assertEqual(whatsapp_fetch({"api_key": "x"})["events"], [])

    def test_slack_requires_channel_ids_when_token_is_set(self) -> None:
        result = slack_fetch({"bot_token": "x"})
        self.assertEqual(result["events"], [])
        self.assertIn("channel_ids", result["warning"])
        status = health("slack", {"bot_token": "x"})
        self.assertFalse(status["healthy"])
        self.assertEqual(status["mode"], "unconfigured")

    def test_todoist_onboarding_filter_is_deterministic(self) -> None:
        config = {
            "exclude_project_ids": ["tutorial"],
            "exclude_labels": ["template"],
            "exclude_onboarding": True,
        }
        self.assertTrue(is_todoist_onboarding_task({"content": "Welcome to Todoist"}, config))
        self.assertTrue(is_todoist_onboarding_task({"content": "Quarterly planning", "project_id": "tutorial"}, config))
        self.assertTrue(is_todoist_onboarding_task({"content": "Reusable task", "labels": ["template"]}, config))
        self.assertFalse(is_todoist_onboarding_task({"content": "Review payroll"}, config))

    def test_google_request_refreshes_stale_access_token(self) -> None:
        def fake_http(method, url, *, headers=None, payload=None, timeout=20):
            self.assertEqual(headers["Authorization"], "Bearer fresh-token")
            return {"ok": True}

        with (
            patch("scripts.external_cli.google_oauth_credentials", return_value={"access_token": "old-token"}),
            patch("scripts.external_cli.can_refresh_google_token", return_value=True),
            patch("scripts.external_cli.google_token_is_stale", return_value=True),
            patch("scripts.external_cli.refresh_google_access_token", return_value="fresh-token") as refresh,
            patch("scripts.external_cli.http_json", fake_http),
        ):
            self.assertEqual(
                google_http_json({}, method="GET", url="https://example.test")["ok"],
                True,
            )
            refresh.assert_called_once()

    def test_google_request_retries_once_after_unauthorized_refresh(self) -> None:
        calls: list[str] = []

        def fake_http(method, url, *, headers=None, payload=None, timeout=20):
            calls.append(headers["Authorization"])
            if len(calls) == 1:
                raise RuntimeError("HTTP 401: expired")
            return {"ok": True}

        with (
            patch("scripts.external_cli.google_oauth_credentials", return_value={"access_token": "old-token"}),
            patch("scripts.external_cli.can_refresh_google_token", return_value=True),
            patch("scripts.external_cli.google_token_is_stale", return_value=False),
            patch("scripts.external_cli.refresh_google_access_token", return_value="retry-token"),
            patch("scripts.external_cli.http_json", fake_http),
        ):
            self.assertEqual(google_http_json({}, method="GET", url="https://example.test")["ok"], True)
        self.assertEqual(calls, ["Bearer old-token", "Bearer retry-token"])

    def test_google_refresh_failure_is_explicit(self) -> None:
        with (
            patch("scripts.external_cli.google_oauth_credentials", return_value={"access_token": "old-token"}),
            patch("scripts.external_cli.can_refresh_google_token", return_value=True),
            patch("scripts.external_cli.google_token_is_stale", return_value=True),
            patch("scripts.external_cli.refresh_google_access_token", side_effect=RuntimeError("refresh failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "refresh failed"):
                google_http_json({}, method="GET", url="https://example.test")


if __name__ == "__main__":
    unittest.main()
