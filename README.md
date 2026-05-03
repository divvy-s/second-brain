# Second Brain

Second Brain is a plugin-driven AI operating system that ingests context from apps, stores it in hybrid memory, reasons over it with a single LLM adapter, and executes actions through a risk-aware approval gate.

## 🗂️ Project Structure

The project is highly modular, split into distinct domain boundaries to ensure separation of concerns between ingestion, reasoning, and execution.

| Directory | Description |
|-----------|-------------|
| **`agents/`** | Autonomous agents that reason over context and propose actions. |
| **`api/`** | FastAPI-based HTTP server exposing endpoints for the frontend and webhooks. |
| **`config/`** | YAML configuration files (e.g., `user_config.yml`) for runtime settings. |
| **`connectors/`** | Built-in integrations (MCP plugins) like Slack, Gmail, Telegram, etc. |
| **`data/`** | Local storage directory (e.g., SQLite databases, entity models). |
| **`execution/`** | The action-taking engine and risk-aware approval gate. |
| **`frontend/`** | Web UI for interacting with your Second Brain. |
| **`intelligence/`** | OpenAI-compatible LLM adapter, intent parsing, ranking, and routing logic. |
| **`memory/`** | Hybrid memory system for ingesting, storing, and retrieving past context. |
| **`orchestration/`** | Event bus and workflow routing (optionally backed by Redis). |
| **`scripts/`** | Utility scripts for database initialization, ingestion, and running the server. |
| **`second_brain_sdk/`** | SDK containing base classes like `MCPConnector` to build new plugins. |
| **`tests/`** | Pytest suites covering unit and integration testing. |
| **`user_plugins/`** | Custom, user-defined plugins that drop right into the system. |

## 🚀 Setup and Configuration

> **Note:** For comprehensive step-by-step run instructions, troubleshooting, and Docker usage, see [**run.md**](./run.md).

### Prerequisites
- Python 3.11+
- Node.js 18+ & npm 9+
- Redis (optional, for persistent event bus)

### Quick Start
1. **Environment Variables**:
   Copy `.env.example` to `.env` and fill in your API keys (for the default config, `GEMINI_API_KEY`):
   ```bash
   cp .env.example .env
   ```
2. **Install Dependencies & Initialize**:
   ```bash
   pip install .
   python -m spacy download en_core_web_sm
   python -m scripts.init_db
   ```
3. **Run API (Terminal 1)**:
   ```bash
   python -m scripts.run_api
   ```
4. **Run Frontend (Terminal 2)**:
   ```bash
   cd frontend
   npm install
   npm run dev
   ```
The API runs on `http://127.0.0.1:8000` and the frontend usually on `http://localhost:5173`.

## 🛡️ Security Model

External integrations are loaded as plugins and reach provider APIs through `scripts.external_cli`. Credentials are referenced through environment variables or local config and are never passed into LLM prompts. All LLM calls go through `intelligence.llm_adapter.LLMAdapter`, which reads `llm.primary.provider`, `base_url`, `api_key_env`, and `model` from `config/user_config.yml`. The default provider is Gemini through its OpenAI-compatible endpoint; OpenAI can be used by setting `provider: "openai"`, `base_url: "https://api.openai.com/v1"`, `api_key_env: "OPENAI_API_KEY"`, and an OpenAI model such as `gpt-4o-mini`.

Set `ENVIRONMENT=development` for local auth bypass while `SECRET_KEY` is empty. In production, leave `ENVIRONMENT=production` and set a strong random `SECRET_KEY`; startup now fails clearly if it is missing or too short. The frontend must use the same value as `VITE_API_KEY` so protected API calls send `Authorization: Bearer ...`.

Expensive endpoints such as `/brain-dump` and `/orchestrate` are rate-limited with configurable values in `config/user_config.yml`.

## Webhooks and Sync

Telegram and WhatsApp inbound messages are webhook-only by default. Telegram should deliver updates to `POST /telegram/webhook` with `TELEGRAM_WEBHOOK_SECRET` configured as the `X-Telegram-Bot-Api-Secret-Token`; do not enable Telegram polling while a webhook is active. WhatsApp Cloud API should verify and deliver to `/whatsapp/webhook` using `WHATSAPP_VERIFY_TOKEN`.

For local Telegram setup, start the API with `ENVIRONMENT=development`. If `TELEGRAM_WEBHOOK_SECRET` is blank, the server creates a runtime-only secret and exposes setup details at `GET /telegram/webhook/setup?public_url=https://<your-ngrok-host>`. Use the returned `set_webhook_url` with BotFather/API credentials; it has the shape `https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook?url=https://<your-ngrok-host>/telegram/webhook&secret_token=<TELEGRAM_WEBHOOK_SECRET>`.

Use `POST /events/sync` to fetch and store connector data without running orchestration. Use `POST /orchestrate` only when you want reasoning, recommendations, and approval generation.

Google Gmail and Calendar integrations refresh access tokens automatically when `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REFRESH_TOKEN` are available, or after completing `/auth/google`.

Slack fetching requires `plugins.slack.channel_ids` in `config/user_config.yml` or `SLACK_CHANNEL_IDS`; a bot token alone is not enough to choose channels.

## 🔌 Plugin System

Built-in MCP plugins live in `connectors/mcp/`. User plugins live in `user_plugins/` and can subclass `second_brain_sdk.MCPConnector`.

```python
from second_brain_sdk import MCPConnector

class WhatsAppPlugin(MCPConnector):
    name = "whatsapp"
    version = "1.0.0"
    auth_type = "api_key"
```

The registry at `connectors/mcp/plugin_registry.json` records built-in plugins. Runtime enablement and secrets live in `config/user_config.yml`.

