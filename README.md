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
| **`intelligence/`** | LLM adapters (`xAI Grok`, `OpenRouter` fallback) and routing logic. |
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

### Quick Start (Windows / PowerShell)
These steps run the API locally, with either SQLite (default) or PostgreSQL via `DATABASE_URL`.

1. **Create a virtualenv**

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -U pip
```

2. **Install dependencies (recommended: editable + dev deps)**

```powershell
python -m pip install -e ".[dev]"
python -m spacy download en_core_web_sm
```

3. **Create `.env`**
- Copy `.env.example` → `.env`
- Fill in keys you will actually use.

Minimum for the API to start:
- `SECRET_KEY` (used for Bearer auth; see `config/user_config.yml` → `api.auth_token_env`)

To use PostgreSQL instead of SQLite:
- Set `DATABASE_URL=postgresql://...`

4. **Initialize the database schema**

```powershell
python scripts\init_db.py
```

5. **Run the API**

```powershell
python scripts\run_api.py
```

API:
- Health: `http://127.0.0.1:8000/health`
- Docs: `http://127.0.0.1:8000/docs`
- Audit logs: `GET /audit/logs`
- Manual proactive cycle: `POST /proactive/run-once`

6. **Run the frontend (optional)**

Create `frontend/.env` with the API host and token you want the browser app to use:

```env
VITE_API_BASE=http://127.0.0.1:8000
VITE_API_TOKEN=your_same_secret_key_value
```

```powershell
cd frontend
npm install
npm run dev
```

Frontend typically runs on `http://localhost:5173`.

### Continuous proactive loop
The system now includes a continuous orchestration loop that runs:

`Ingest -> Store -> Score -> Rank -> Retrieve -> Plan -> Route -> Execute`

By default, the API process starts this loop automatically every 30 minutes. You can control it with:

- `SECOND_BRAIN_PROACTIVE_LOOP_ENABLED`
- `SECOND_BRAIN_PROACTIVE_LOOP_INTERVAL_MINUTES`
- `SECOND_BRAIN_STALE_APPROVAL_MINUTES`

If you prefer to run the loop as a standalone worker instead of inside the API process:

```powershell
python scripts\run_proactive_loop.py
```

The loop records a summary of each cycle and automatically marks long-pending approvals as ignored so that negative feedback feeds back into ranking.

### Render PostgreSQL (getting `DATABASE_URL`)
If you want a hosted PostgreSQL instance:
1. Render Dashboard → **New** → **PostgreSQL**
2. Create the database
3. In the database page, copy the connection string:
   - **External Database URL**: use from your laptop / local dev
   - **Internal Database URL**: use from a Render Web Service in the same account
4. Paste into `.env`:

```env
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME
```

If your provider requires SSL and the URL doesn't already include it, append:
`?sslmode=require`

Then rerun:

```powershell
python scripts\init_db.py
```

If you already have data in SQLite and want to move it into PostgreSQL, run:

```powershell
python scripts\migrate_sqlite_to_postgres.py
```

The migration script expects an empty PostgreSQL database and preserves the existing SQLite row IDs and relationships.

### Telegram webhook (ngrok)
This project exposes a Telegram webhook endpoint at:
- `POST /telegram/webhook`

To receive Telegram updates locally, you need a public HTTPS URL (e.g. ngrok) that forwards to your local API.

1. Start the API (`python scripts\run_api.py`)
2. Start ngrok for port 8000:

```powershell
ngrok http 8000
```

3. Set these env vars in `.env`:
- `TELEGRAM_BOT_TOKEN` (from BotFather)
- `TELEGRAM_CHAT_ID` (your chat ID)
- `TELEGRAM_WEBHOOK_SECRET` (generate one; recommended)

Generate a secret:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

4. Set webhook in Telegram to the **ngrok URL + `/telegram/webhook`** and include the same secret:

```text
https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<your-ngrok-host>/telegram/webhook&secret_token=<TELEGRAM_WEBHOOK_SECRET>
```

### Troubleshooting
#### Importing the wrong `api` / `memory` package (Windows)
If you see errors like:
- `ModuleNotFoundError: No module named 'memory.backends'`
- or paths pointing into `...Python...\\Lib\\site-packages\\api\\...`

You likely have a conflicting installed package named `api`/`memory` or an older installed build of this repo.

Fix:
- Use a fresh virtualenv (recommended), and install with `python -m pip install -e ".[dev]"`
- Ensure you run scripts from the repo root, e.g. `python scripts\run_api.py`

## 🛡️ Security Model

External integrations are loaded as plugins and reach provider APIs through `scripts.external_cli`. Credentials are referenced through environment variables or local config and are never passed into LLM prompts. All LLM calls go through `intelligence.llm_adapter.LLMAdapter`, with xAI Grok as the primary OpenAI-compatible provider and OpenRouter as fallback.

The action path now includes:

- numeric risk scoring before approval
- approval, reject, and ignore states
- audit logging for each action attempt
- rollback support through `POST /actions/rollback`
- feedback capture that adjusts event importance and reinforcement

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

