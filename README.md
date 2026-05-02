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

### Quick Start
1. **Environment Variables**:
   Copy `.env.example` to `.env` and fill in your API keys (e.g., `XAI_API_KEY`):
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

External integrations are loaded as plugins and reach provider APIs through `scripts.external_cli`. Credentials are referenced through environment variables or local config and are never passed into LLM prompts. All LLM calls go through `intelligence.llm_adapter.LLMAdapter`, with xAI Grok as the primary OpenAI-compatible provider and OpenRouter as fallback.

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

