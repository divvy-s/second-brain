# Second Brain

Second Brain is a plugin-driven AI operating system that ingests context from apps, stores it in hybrid memory, reasons over it with a single LLM adapter, and executes actions through a risk-aware approval gate.

## Setup and Configuration

### Prerequisites
- Python 3.11+
- Node.js & npm (for frontend)
- Redis (optional, for event bus)

### Backend Setup
1. **Environment Variables**:
   Copy `.env.example` to `.env` and fill in your API keys:
   ```bash
   cp .env.example .env
   ```
2. **Install Dependencies**:
   ```bash
   pip install .
   ```
3. **Initialize Database**:
   ```bash
   python -m scripts.init_db
   ```
4. **Run API**:
   ```bash
   python -m scripts.run_api
   ```

### Frontend Setup
1. **Install Dependencies**:
   ```bash
   cd frontend
   npm install
   ```
2. **Run Dev Server**:
   ```bash
   npm run dev
   ```

## Quick Start
To get everything running quickly:
```bash
# Terminal 1: Backend
python -m scripts.run_api

# Terminal 2: Frontend
cd frontend && npm run dev
```
The API runs on `http://127.0.0.1:8000` and the frontend usually on `http://localhost:5173`.


## Security Model

External integrations are loaded as plugins and reach provider APIs through `scripts.external_cli`. Credentials are referenced through environment variables or local config and are never passed into LLM prompts. All LLM calls go through `intelligence.llm_adapter.LLMAdapter`, with xAI Grok as the primary OpenAI-compatible provider and OpenRouter as fallback.

## Plugin Layout

Built-in MCP plugins live in `connectors/mcp/`. User plugins live in `user_plugins/` and can subclass `second_brain_sdk.MCPConnector`.

```python
from second_brain_sdk import MCPConnector

class WhatsAppPlugin(MCPConnector):
    name = "whatsapp"
    version = "1.0.0"
    auth_type = "api_key"
```

The registry at `connectors/mcp/plugin_registry.json` records built-in plugins. Runtime enablement and secrets live in `config/user_config.yml`.

