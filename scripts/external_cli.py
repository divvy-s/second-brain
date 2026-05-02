from __future__ import annotations

import argparse
import base64
import email.message
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_secret(config: dict[str, Any], *keys: str) -> str:
    for key in keys:
        env_key = config.get(f"{key}_env")
        if env_key:
            return os.environ.get(str(env_key), "")
        value = config.get(key)
        if isinstance(value, str) and value.startswith("env:"):
            return os.environ.get(value.removeprefix("env:"), "")
        if isinstance(value, str):
            return value
    return ""


def respond(payload: dict[str, Any], status: int = 0) -> int:
    sys.stdout.write(json.dumps(payload, separators=(",", ":")))
    return status


def read_request() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {"config": {}, "payload": {}}
    return json.loads(raw)


def http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    body = None
    request_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, method=method, data=body, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read().decode("utf-8")
            if not data:
                return {}
            return json.loads(data)
    except urllib.error.HTTPError as exc:
        data = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {data[:300]}") from exc


def http_form(url: str, payload: dict[str, Any], *, timeout: int = 20) -> dict[str, Any]:
    encoded = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        method="POST",
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read().decode("utf-8")
            return json.loads(data or "{}")
    except urllib.error.HTTPError as exc:
        data = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {data[:300]}") from exc


def normalize_mock_events(service: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    events = []
    for item in config.get("mock_events", []) or []:
        event = dict(item)
        event.setdefault("id", f"{service}-{int(time.time() * 1000)}")
        event.setdefault("source", f"mcp_{service}")
        event.setdefault("kind", "event")
        event.setdefault("title", event.get("body", service).splitlines()[0][:80])
        event.setdefault("body", "")
        event.setdefault("occurred_at", utc_now_iso())
        event.setdefault("participants", [])
        event.setdefault("importance", 0.5)
        event.setdefault("metadata", {"mode": "mock"})
        events.append(event)
    return events


def has_mock_events(config: dict[str, Any]) -> bool:
    return bool(config.get("mock_events"))


def google_oauth_credentials(config: dict[str, Any]) -> dict[str, str]:
    return {
        "client_id": resolve_secret(config, "client_id") or os.environ.get("GOOGLE_CLIENT_ID", ""),
        "client_secret": resolve_secret(config, "client_secret") or os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        "refresh_token": resolve_secret(config, "refresh_token") or os.environ.get("GOOGLE_REFRESH_TOKEN", ""),
    }


def can_refresh_google_token(config: dict[str, Any]) -> bool:
    creds = google_oauth_credentials(config)
    return all(creds.values())


def refresh_google_access_token(config: dict[str, Any]) -> str:
    creds = google_oauth_credentials(config)
    if not all(creds.values()):
        return ""
    response = http_form(
        "https://oauth2.googleapis.com/token",
        {
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": creds["refresh_token"],
            "grant_type": "refresh_token",
        },
    )
    return str(response.get("access_token") or "")


def google_http_json(
    config: dict[str, Any],
    *,
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    token = resolve_secret(config, "access_token")
    for attempt in range(2):
        if not token and can_refresh_google_token(config):
            token = refresh_google_access_token(config)
        if not token:
            raise RuntimeError("No Google access token or refresh credentials are configured")
        try:
            return http_json(
                method,
                url,
                headers={"Authorization": f"Bearer {token}"},
                payload=payload,
                timeout=timeout,
            )
        except RuntimeError as exc:
            if "HTTP 401" in str(exc) and attempt == 0 and can_refresh_google_token(config):
                token = refresh_google_access_token(config)
                continue
            raise
    raise RuntimeError("Google API request failed after token refresh")


def gmail_fetch(config: dict[str, Any]) -> dict[str, Any]:
    token = resolve_secret(config, "access_token")
    if not token and not can_refresh_google_token(config):
        return {"ok": True, "events": normalize_mock_events("gmail", config)}
    max_results = int(config.get("max_results", 10))
    list_url = (
        "https://gmail.googleapis.com/gmail/v1/users/me/messages?"
        + urllib.parse.urlencode({"maxResults": max_results, "q": config.get("query", "newer_than:7d")})
    )
    listed = google_http_json(config, method="GET", url=list_url)
    events: list[dict[str, Any]] = []
    for message in listed.get("messages", []):
        msg_id = message["id"]
        detail_url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}?format=metadata"
        detail = google_http_json(config, method="GET", url=detail_url)
        headers_list = detail.get("payload", {}).get("headers", [])
        header_map = {item.get("name", "").lower(): item.get("value", "") for item in headers_list}
        events.append(
            {
                "id": f"gmail-{msg_id}",
                "source": "mcp_gmail",
                "kind": "email",
                "title": header_map.get("subject") or "(no subject)",
                "body": detail.get("snippet", ""),
                "occurred_at": datetime.fromtimestamp(int(detail.get("internalDate", "0")) / 1000, timezone.utc).isoformat(),
                "participants": [header_map.get("from", "")],
                "importance": 0.6,
                "metadata": {"thread_id": detail.get("threadId"), "provider_id": msg_id},
            }
        )
    return {"ok": True, "events": events}


def gmail_action(config: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    action_type = action.get("type")
    if action_type in {"send_email", "draft_email"}:
        message = email.message.EmailMessage()
        if action.get("to"):
            message["To"] = action["to"]
        message["Subject"] = action.get("subject", action.get("title", ""))
        message.set_content(action.get("body", action.get("text", "")))
        encoded = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        if action_type == "send_email":
            result = google_http_json(
                config,
                method="POST",
                url="https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                payload={"raw": encoded},
            )
            return {"ok": True, "result": {"provider_id": result.get("id"), "status": "sent"}}
        result = google_http_json(
            config,
            method="POST",
            url="https://gmail.googleapis.com/gmail/v1/users/me/drafts",
            payload={"message": {"raw": encoded}},
        )
        return {"ok": True, "result": {"provider_id": result.get("id"), "status": "drafted"}}
    if action_type == "mark_read":
        provider_id = action["provider_id"]
        url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{provider_id}/modify"
        result = google_http_json(config, method="POST", url=url, payload={"removeLabelIds": ["UNREAD"]})
        return {"ok": True, "result": {"provider_id": result.get("id"), "status": "read"}}
    raise ValueError(f"Unsupported Gmail action type: {action_type}")


def slack_fetch(config: dict[str, Any]) -> dict[str, Any]:
    token = resolve_secret(config, "bot_token", "api_key")
    channels = config.get("channel_ids") or []
    if not token or not channels:
        return {"ok": True, "events": normalize_mock_events("slack", config)}
    headers = {"Authorization": f"Bearer {token}"}
    events: list[dict[str, Any]] = []
    for channel_id in channels:
        url = "https://slack.com/api/conversations.history?" + urllib.parse.urlencode(
            {"channel": channel_id, "limit": int(config.get("limit", 20))}
        )
        data = http_json("GET", url, headers=headers)
        if not data.get("ok"):
            raise RuntimeError(data.get("error", "Slack API error"))
        for item in data.get("messages", []):
            ts = float(item.get("ts", time.time()))
            events.append(
                {
                    "id": f"slack-{channel_id}-{item.get('ts')}",
                    "source": "mcp_slack",
                    "kind": "message",
                    "title": f"Slack message in {channel_id}",
                    "body": item.get("text", ""),
                    "occurred_at": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
                    "participants": [item.get("user", "")],
                    "importance": 0.55,
                    "metadata": {"channel": channel_id, "ts": item.get("ts")},
                }
            )
    return {"ok": True, "events": events}


def slack_action(config: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    token = resolve_secret(config, "bot_token", "api_key")
    if not token:
        raise RuntimeError("Slack bot token is required for write actions")
    if action.get("type") != "post_message":
        raise ValueError(f"Unsupported Slack action type: {action.get('type')}")
    data = http_json(
        "POST",
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {token}"},
        payload={"channel": action["channel"], "text": action["text"]},
    )
    if not data.get("ok"):
        raise RuntimeError(data.get("error", "Slack API error"))
    return {"ok": True, "result": {"channel": data.get("channel"), "ts": data.get("ts"), "status": "posted"}}


def telegram_fetch(config: dict[str, Any]) -> dict[str, Any]:
    token = resolve_secret(config, "bot_token")
    if not token:
        return {"ok": True, "events": normalize_mock_events("telegram", config)}
    offset = config.get("offset")
    query = {"timeout": 0}
    if offset:
        query["offset"] = offset
    url = f"https://api.telegram.org/bot{token}/getUpdates?" + urllib.parse.urlencode(query)
    data = http_json("GET", url)
    if not data.get("ok"):
        raise RuntimeError("Telegram API error")
    events: list[dict[str, Any]] = []
    for update in data.get("result", []):
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat", {})
        sender = message.get("from", {})
        events.append(
            {
                "id": f"telegram-{update.get('update_id')}",
                "source": "mcp_telegram",
                "kind": "message",
                "title": f"Telegram message from {sender.get('username') or sender.get('first_name') or chat.get('id')}",
                "body": message.get("text", ""),
                "occurred_at": datetime.fromtimestamp(message.get("date", time.time()), timezone.utc).isoformat(),
                "participants": [str(sender.get("username") or sender.get("id") or "")],
                "importance": 0.5,
                "metadata": {"chat_id": chat.get("id"), "update_id": update.get("update_id")},
            }
        )
    return {"ok": True, "events": events}


def telegram_action(config: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    token = resolve_secret(config, "bot_token")
    chat_id = action.get("chat_id") or action.get("recipient") or resolve_secret(config, "chat_id")
    if not token:
        raise RuntimeError("Telegram bot token is required for write actions")
    if not chat_id:
        raise ValueError("Telegram chat_id is required (action.chat_id or config.chat_id)")
    if action.get("type") != "send_message":
        raise ValueError(f"Unsupported Telegram action type: {action.get('type')}")
    data = http_json(
        "POST",
        f"https://api.telegram.org/bot{token}/sendMessage",
        payload={"chat_id": chat_id, "text": action["text"]},
    )
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data.get('description', 'Unknown error')}")
    return {"ok": True, "result": {"message_id": data.get("result", {}).get("message_id"), "status": "sent"}}


def whatsapp_fetch(config: dict[str, Any]) -> dict[str, Any]:
    base_url = str(config.get("base_url") or "").rstrip("/")
    api_key = resolve_secret(config, "api_key")
    if not base_url:
        return {"ok": True, "events": normalize_mock_events("whatsapp", config)}
    data = http_json("GET", f"{base_url}/events", headers={"Authorization": f"Bearer {api_key}"} if api_key else {})
    events = []
    for item in data.get("events", []):
        event = dict(item)
        event.setdefault("source", "mcp_whatsapp")
        event.setdefault("kind", "message")
        event.setdefault("occurred_at", utc_now_iso())
        event.setdefault("participants", [])
        event.setdefault("importance", 0.5)
        events.append(event)
    return {"ok": True, "events": events}


def whatsapp_action(config: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    api_key = resolve_secret(config, "api_key")
    phone_id = resolve_secret(config, "phone_number_id")
    recipient = action.get("to") or action.get("recipient") or resolve_secret(config, "recipient_id")
    if not api_key or not phone_id:
        raise RuntimeError("WhatsApp API Key and Phone Number ID are required")
    if not recipient:
        raise ValueError("Recipient phone number is required (action.to or config.recipient_id)")
    recipient = "".join(filter(str.isdigit, str(recipient)))
    api_version = str(config.get("api_version") or os.environ.get("WHATSAPP_API_VERSION", "v22.0"))
    url = f"https://graph.facebook.com/{api_version}/{phone_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "text",
        "text": {"body": action.get("text", "No message content provided")},
    }
    data = http_json("POST", url, headers={"Authorization": f"Bearer {api_key}"}, payload=payload)
    return {"ok": True, "result": data}


def calendar_fetch(config: dict[str, Any]) -> dict[str, Any]:
    token = resolve_secret(config, "access_token")
    if not token and not can_refresh_google_token(config):
        return {"ok": True, "events": normalize_mock_events("calendar", config)}
    now = datetime.now(timezone.utc).isoformat()
    url = (
        "https://www.googleapis.com/calendar/v3/calendars/primary/events?"
        + urllib.parse.urlencode({"timeMin": now, "maxResults": 10, "singleEvents": "true", "orderBy": "startTime"})
    )
    data = google_http_json(config, method="GET", url=url)
    events: list[dict[str, Any]] = []
    for item in data.get("items", []):
        events.append(
            {
                "id": f"calendar-{item.get('id')}",
                "source": "mcp_calendar",
                "kind": "event",
                "title": item.get("summary", "(no title)"),
                "body": item.get("description", ""),
                "occurred_at": item.get("start", {}).get("dateTime", item.get("start", {}).get("date", utc_now_iso())),
                "participants": [participant.get("email", "") for participant in item.get("attendees", [])],
                "importance": 0.8,
                "metadata": {"event_id": item.get("id"), "link": item.get("htmlLink")},
            }
        )
    return {"ok": True, "events": events}


def calendar_action(config: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    action_type = action.get("type")
    if action_type not in ("create_calendar_draft", "create_event", "create_calendar_event"):
        raise ValueError(f"Unsupported Calendar action type: {action_type}")
    start_time = action.get("start_time")
    end_time = action.get("end_time")
    if not start_time:
        start_time_dt = datetime.now(timezone.utc) + timedelta(hours=1)
        start_time = start_time_dt.isoformat()
    else:
        try:
            start_time_dt = datetime.fromisoformat(str(start_time).replace("Z", "+00:00"))
        except ValueError:
            start_time_dt = datetime.now(timezone.utc) + timedelta(hours=1)
            start_time = start_time_dt.isoformat()
    if not end_time:
        end_time = (start_time_dt + timedelta(hours=1)).isoformat()
    payload = {
        "summary": action.get("title", "New Event"),
        "description": action.get("description", ""),
        "start": {"dateTime": start_time},
        "end": {"dateTime": end_time},
    }
    data = google_http_json(
        config,
        method="POST",
        url="https://www.googleapis.com/calendar/v3/calendars/primary/events",
        payload=payload,
    )
    return {"ok": True, "result": {"event_id": data.get("id"), "link": data.get("htmlLink"), "status": "created"}}


def todoist_fetch(config: dict[str, Any]) -> dict[str, Any]:
    token = resolve_secret(config, "api_key")
    if not token:
        return {"ok": True, "events": normalize_mock_events("todoist", config)}
    data = http_json("GET", "https://api.todoist.com/api/v1/tasks", headers={"Authorization": f"Bearer {token}"})
    events: list[dict[str, Any]] = []
    items = data if isinstance(data, list) else data.get("items", [])
    for item in items:
        events.append(
            {
                "id": f"todoist-{item.get('id')}",
                "source": "mcp_todoist",
                "kind": "task",
                "title": item.get("content", "(no title)"),
                "body": item.get("description", ""),
                "occurred_at": item.get("created_at", utc_now_iso()),
                "participants": [],
                "importance": 0.6,
                "metadata": {"task_id": item.get("id"), "url": item.get("url")},
            }
        )
    return {"ok": True, "events": events}


def todoist_action(config: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    token = resolve_secret(config, "api_key")
    if not token:
        raise RuntimeError("Todoist API key is required for write actions")
    if action.get("type") != "create_task":
        raise ValueError(f"Unsupported Todoist action type: {action.get('type')}")
    payload = {"content": action.get("title", "New Task"), "description": action.get("description", "")}
    data = http_json("POST", "https://api.todoist.com/api/v1/tasks", headers={"Authorization": f"Bearer {token}"}, payload=payload)
    return {"ok": True, "result": {"task_id": data.get("id"), "url": data.get("url"), "status": "created"}}


def health(service: str, config: dict[str, Any]) -> dict[str, Any]:
    mock_enabled = has_mock_events(config)
    if service in {"gmail", "calendar"}:
        live_configured = bool(resolve_secret(config, "access_token")) or can_refresh_google_token(config)
        if not live_configured:
            return {"ok": True, "healthy": False, "mode": "mock" if mock_enabled else "unconfigured", "mock_enabled": mock_enabled}
        try:
            url = (
                "https://gmail.googleapis.com/gmail/v1/users/me/profile"
                if service == "gmail"
                else "https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults=1"
            )
            google_http_json(config, method="GET", url=url)
            return {"ok": True, "healthy": True, "mode": "live", "mock_enabled": mock_enabled}
        except Exception as exc:
            return {"ok": True, "healthy": False, "mode": "error", "mock_enabled": mock_enabled, "error": str(exc)}
    if service == "slack":
        token = resolve_secret(config, "bot_token", "api_key")
        if not token:
            return {"ok": True, "healthy": False, "mode": "mock" if mock_enabled else "unconfigured", "mock_enabled": mock_enabled}
        try:
            data = http_json("GET", "https://slack.com/api/auth.test", headers={"Authorization": f"Bearer {token}"})
            if not data.get("ok"):
                raise RuntimeError(data.get("error", "Slack API error"))
            return {"ok": True, "healthy": True, "mode": "live", "mock_enabled": mock_enabled}
        except Exception as exc:
            return {"ok": True, "healthy": False, "mode": "error", "mock_enabled": mock_enabled, "error": str(exc)}
    if service == "telegram":
        token = resolve_secret(config, "bot_token")
        if not token:
            return {"ok": True, "healthy": False, "mode": "mock" if mock_enabled else "unconfigured", "mock_enabled": mock_enabled}
        try:
            data = http_json("GET", f"https://api.telegram.org/bot{token}/getMe")
            if not data.get("ok"):
                raise RuntimeError(data.get("description", "Telegram API error"))
            return {"ok": True, "healthy": True, "mode": "live", "mock_enabled": mock_enabled}
        except Exception as exc:
            return {"ok": True, "healthy": False, "mode": "error", "mock_enabled": mock_enabled, "error": str(exc)}
    if service == "whatsapp":
        api_key = resolve_secret(config, "api_key")
        phone_id = resolve_secret(config, "phone_number_id")
        if not api_key or not phone_id:
            return {"ok": True, "healthy": False, "mode": "mock" if mock_enabled else "unconfigured", "mock_enabled": mock_enabled}
        try:
            api_version = str(config.get("api_version") or os.environ.get("WHATSAPP_API_VERSION", "v22.0"))
            http_json("GET", f"https://graph.facebook.com/{api_version}/{phone_id}", headers={"Authorization": f"Bearer {api_key}"})
            return {"ok": True, "healthy": True, "mode": "live", "mock_enabled": mock_enabled}
        except Exception as exc:
            return {"ok": True, "healthy": False, "mode": "error", "mock_enabled": mock_enabled, "error": str(exc)}
    if service == "todoist":
        token = resolve_secret(config, "api_key")
        if not token:
            return {"ok": True, "healthy": False, "mode": "mock" if mock_enabled else "unconfigured", "mock_enabled": mock_enabled}
        try:
            http_json("GET", "https://api.todoist.com/api/v1/tasks?limit=1", headers={"Authorization": f"Bearer {token}"})
            return {"ok": True, "healthy": True, "mode": "live", "mock_enabled": mock_enabled}
        except Exception as exc:
            return {"ok": True, "healthy": False, "mode": "error", "mock_enabled": mock_enabled, "error": str(exc)}
    return {"ok": False, "healthy": False, "error": "unknown service"}


FETCHERS = {
    "gmail": gmail_fetch,
    "slack": slack_fetch,
    "telegram": telegram_fetch,
    "whatsapp": whatsapp_fetch,
    "calendar": calendar_fetch,
    "todoist": todoist_fetch,
}

ACTIONS = {
    "gmail": gmail_action,
    "slack": slack_action,
    "telegram": telegram_action,
    "whatsapp": whatsapp_action,
    "calendar": calendar_action,
    "todoist": todoist_action,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Second Brain external API wrapper")
    parser.add_argument("service", choices=sorted(FETCHERS))
    parser.add_argument("operation", choices=["fetch_events", "execute_action", "health_check"])
    args = parser.parse_args()
    request = read_request()
    config = request.get("config", {}) or {}
    payload = request.get("payload", {}) or {}
    try:
        if args.operation == "fetch_events":
            return respond(FETCHERS[args.service](config))
        if args.operation == "execute_action":
            return respond(ACTIONS[args.service](config, payload))
        return respond(health(args.service, config))
    except Exception as exc:
        return respond({"ok": False, "error": str(exc)}, status=1)


if __name__ == "__main__":
    raise SystemExit(main())
