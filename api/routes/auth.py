"""
OAuth2 PKCE flow for Google services (Gmail, Calendar).

Replaces manual token pasting in .env with a secure browser-based consent flow.
Tokens are stored in the oauth_tokens SQLite table, not on disk.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import time
import urllib.parse
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

router = APIRouter(prefix="/auth", tags=["auth"])

# In-memory store for PKCE state → code_verifier mapping (short-lived)
_pending_states: dict[str, dict[str, str]] = {}

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]


def _get_db():
    """Get the database from the running app services."""
    from api.dependencies import build_services
    # This is a lightweight call — build_services caches internally via app.state
    # For the auth routes, we access the DB directly
    from pathlib import Path
    from memory.database import MemoryDatabase
    from connectors.config import load_config
    app_root = Path(__file__).resolve().parents[2]
    config = load_config(app_root / "config" / "user_config.yml")
    memory_config = config.get("memory", {})
    db = MemoryDatabase(app_root / memory_config.get("sqlite_path", "data/second_brain.sqlite3"))
    return db


@router.get("/google")
def google_auth_start(request: Request):
    """Redirect to Google's OAuth2 consent screen using PKCE."""
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    if not client_id:
        raise HTTPException(status_code=503, detail="GOOGLE_CLIENT_ID is not configured in .env")

    # Generate PKCE code_verifier and code_challenge
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = hashlib.sha256(code_verifier.encode()).digest()
    import base64
    code_challenge_b64 = base64.urlsafe_b64encode(code_challenge).rstrip(b"=").decode()

    state = secrets.token_urlsafe(32)
    _pending_states[state] = {"code_verifier": code_verifier}

    # Determine callback URL from the incoming request
    callback_url = str(request.url_for("google_auth_callback"))

    params = {
        "client_id": client_id,
        "redirect_uri": callback_url,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "state": state,
        "code_challenge": code_challenge_b64,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url=auth_url)


@router.get("/google/callback")
def google_auth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Handle the OAuth2 callback from Google, exchange code for tokens."""
    if error:
        raise HTTPException(status_code=400, detail=f"Google OAuth error: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state parameter")

    pending = _pending_states.pop(state, None)
    if pending is None:
        raise HTTPException(status_code=400, detail="Invalid or expired state parameter")

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    callback_url = str(request.url_for("google_auth_callback"))

    # Exchange authorization code for tokens
    import urllib.request
    import json
    token_data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "code_verifier": pending["code_verifier"],
        "grant_type": "authorization_code",
        "redirect_uri": callback_url,
    }).encode()

    req = urllib.request.Request(GOOGLE_TOKEN_URL, data=token_data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            tokens = json.loads(resp.read().decode())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {exc}")

    access_token = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token", "")
    expires_in = int(tokens.get("expires_in", 3600))
    expires_at = int(time.time()) + expires_in

    if not access_token:
        raise HTTPException(status_code=502, detail="No access_token in Google response")

    # Store tokens in SQLite
    db = _get_db()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO oauth_tokens(provider, access_token, refresh_token, expires_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(provider) DO UPDATE SET
                access_token = excluded.access_token,
                refresh_token = CASE WHEN excluded.refresh_token = '' THEN oauth_tokens.refresh_token ELSE excluded.refresh_token END,
                expires_at = excluded.expires_at
            """,
            ("google", access_token, refresh_token, expires_at),
        )

    return {
        "ok": True,
        "message": "Google OAuth tokens saved successfully. Gmail and Calendar are now connected.",
        "expires_at": expires_at,
    }
