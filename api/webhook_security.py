from __future__ import annotations

import os
import secrets
import urllib.parse
from dataclasses import dataclass
from typing import Callable, Literal


SecretSource = Literal["env", "config", "generated", "missing"]


@dataclass(frozen=True)
class TelegramWebhookSecret:
    secret: str
    source: SecretSource

    @property
    def configured(self) -> bool:
        return bool(self.secret)

    @property
    def generated(self) -> bool:
        return self.source == "generated"


def _telegram_config(config: dict) -> dict:
    return config.setdefault("plugins", {}).setdefault("telegram", {})


def _webhook_secret_env_name(config: dict) -> str:
    telegram_config = _telegram_config(config)
    return str(telegram_config.get("webhook_secret_env", "TELEGRAM_WEBHOOK_SECRET"))


def read_telegram_webhook_secret(config: dict) -> TelegramWebhookSecret:
    telegram_config = _telegram_config(config)
    env_name = _webhook_secret_env_name(config)
    env_secret = str(os.environ.get(env_name, "")).strip()
    if env_secret:
        return TelegramWebhookSecret(env_secret, "env")
    config_secret = str(telegram_config.get("webhook_secret", "")).strip()
    if config_secret:
        return TelegramWebhookSecret(config_secret, "config")
    return TelegramWebhookSecret("", "missing")


def ensure_telegram_webhook_secret(
    config: dict,
    *,
    environment: str,
    generator: Callable[[int], str] = secrets.token_urlsafe,
) -> TelegramWebhookSecret:
    existing = read_telegram_webhook_secret(config)
    if existing.configured:
        return existing

    if environment.lower() in {"dev", "development", "local", "test"}:
        secret = generator(32)
        telegram_config = _telegram_config(config)
        telegram_config["webhook_secret"] = secret
        telegram_config["_webhook_secret_generated"] = True
        return TelegramWebhookSecret(secret, "generated")

    return existing


def telegram_webhook_mode(config: dict) -> str:
    telegram_config = _telegram_config(config)
    return str(telegram_config.get("inbound_mode") or telegram_config.get("mode") or "webhook").lower()


def telegram_polling_enabled(config: dict) -> bool:
    telegram_config = _telegram_config(config)
    return bool(telegram_config.get("polling_enabled", False))


def telegram_webhook_status(config: dict) -> dict[str, object]:
    secret = read_telegram_webhook_secret(config)
    mode = telegram_webhook_mode(config)
    return {
        "mode": mode,
        "secret_configured": secret.configured,
        "secret_source": secret.source,
        "polling_enabled": telegram_polling_enabled(config),
        "safe_for_webhook": mode == "webhook" and secret.configured and not telegram_polling_enabled(config),
    }


def build_telegram_webhook_setup(config: dict, public_base_url: str | None = None) -> dict[str, object]:
    secret = read_telegram_webhook_secret(config)
    if not secret.configured:
        return {
            "configured": False,
            "message": "TELEGRAM_WEBHOOK_SECRET is not configured.",
        }

    base_url = (public_base_url or "https://<your-ngrok-host>").strip().rstrip("/")
    if not base_url:
        base_url = "https://<your-ngrok-host>"
    webhook_url = f"{base_url}/telegram/webhook"
    query = urllib.parse.urlencode({"url": webhook_url, "secret_token": secret.secret})
    readable = (
        "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook"
        f"?url={webhook_url}&secret_token={secret.secret}"
    )
    return {
        "configured": True,
        "secret": secret.secret,
        "secret_source": secret.source,
        "webhook_url": webhook_url,
        "secret_header": "X-Telegram-Bot-Api-Secret-Token",
        "set_webhook_url": f"https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook?{query}",
        "set_webhook_url_display": readable,
        "notes": [
            "Pass secret_token to Telegram setWebhook; Telegram sends it back as a request header.",
            "The local API also accepts a matching secret_token query parameter for ngrok setup testing.",
        ],
    }
