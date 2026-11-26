"""HTTP client for rawandios.dev voice API (auth, quota, live/start)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from urllib.parse import urlencode

import requests

DEFAULT_BASE = "https://rawandios.dev"
API_PREFIX = "/api/voice"


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


CONFIG_PATH = _base_dir() / "config" / "api_keys.json"


def api_base() -> str:
    return os.environ.get("JARVIS_VOICE_API", DEFAULT_BASE).rstrip("/")


def config_path() -> Path:
    return CONFIG_PATH


def read_config() -> dict:
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def ensure_config_file() -> None:
    """Create empty local config from example on first run."""
    if CONFIG_PATH.exists():
        return
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    example = CONFIG_PATH.parent / "api_keys.example.json"
    if example.exists():
        CONFIG_PATH.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        CONFIG_PATH.write_text(
            json.dumps(
                {
                    "access_token": "",
                    "email": "",
                    "os_system": "",
                    "assistant_name": "RAWA DEV",
                    "gemini_voice": "Charon",
                },
                indent=4,
            ),
            encoding="utf-8",
        )


def save_auth(access_token: str, email: str, os_system: str) -> None:
    data = read_config()
    data["access_token"] = access_token
    data["email"] = email
    data["os_system"] = os_system
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_access_token() -> str:
    return (read_config().get("access_token") or "").strip()


def has_auth() -> bool:
    cfg = read_config()
    return bool(cfg.get("access_token")) and bool(cfg.get("os_system"))


class VoiceAPIError(Exception):
    def __init__(self, message: str, status: int = 0, detail: Any = None):
        super().__init__(message)
        self.status = status
        self.detail = detail


class VoiceAPI:
    def __init__(self, base_url: str | None = None):
        self.base = (base_url or api_base()).rstrip("/")

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base}{API_PREFIX}{path}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        json_body: dict | None = None,
    ) -> dict:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            resp = requests.request(
                method,
                self._url(path),
                headers=headers,
                json=json_body,
                timeout=30,
            )
        except requests.RequestException as e:
            raise VoiceAPIError(f"Network error: {e}") from e

        try:
            data = resp.json() if resp.content else {}
        except Exception:
            data = {"raw": resp.text[:500]}

        if resp.ok:
            return data if isinstance(data, dict) else {"data": data}

        detail = data.get("detail") if isinstance(data, dict) else data
        msg = detail if isinstance(detail, str) else str(detail or resp.reason)
        if isinstance(detail, dict) and detail.get("message"):
            msg = detail["message"]
        raise VoiceAPIError(msg, status=resp.status_code, detail=detail)

    def register(self, email: str, password: str) -> dict:
        """Step 1 — sends 6-digit OTP; returns verification `token`."""
        return self._request(
            "POST",
            "/auth/register",
            json_body={"email": email.strip(), "password": password},
        )

    def verify_email(self, verification_token: str, otp: str) -> dict:
        """Step 2 — complete signup; returns access_token."""
        return self._request(
            "POST",
            "/auth/verify-email",
            json_body={"token": verification_token.strip(), "otp": otp.strip()},
        )

    def login(self, email: str, password: str) -> dict:
        return self._request(
            "POST",
            "/auth/login",
            json_body={"email": email.strip(), "password": password},
        )

    def health(self) -> dict:
        return self._request("GET", "/v1/health")

    def quota(self, token: str) -> dict:
        return self._request("GET", "/v1/quota", token=token)

    def live_start(self, token: str, setup: dict | None = None) -> dict:
        body = {"setup": setup} if setup else None
        return self._request("POST", "/v1/live/start", token=token, json_body=body)

    def ws_url(
        self,
        token: str,
        ws_path: str | None = None,
        *,
        live_session_id: str | None = None,
    ) -> str:
        path = ws_path or f"{API_PREFIX}/v1/live"
        if not path.startswith("/"):
            path = "/" + path
        base = self.base
        if base.startswith("https://"):
            host = base[8:]
            scheme = "wss"
        elif base.startswith("http://"):
            host = base[7:]
            scheme = "ws"
        else:
            host = base
            scheme = "wss"
        params = {"access_token": token}
        if live_session_id:
            params["liveSessionId"] = live_session_id
        return f"{scheme}://{host}{path}?{urlencode(params)}"
