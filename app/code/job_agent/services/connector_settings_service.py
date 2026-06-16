from __future__ import annotations

import base64
import hashlib
import secrets
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

import requests

from job_agent.config import ROOT
from job_agent.env import load_env
from job_agent.io.yaml_store import read_yaml, write_yaml
from job_agent.llm import DEFAULT_CLAUDE_MODEL
from job_agent.paths import user_dir

CANVA_MCP_SERVER_URL = "https://mcp.canva.com/mcp"
CANVA_AUTHORIZATION_URL = "https://www.canva.com/api/oauth/authorize"
CANVA_TOKEN_URL = "https://api.canva.com/rest/v1/oauth/token"
CANVA_USER_URL = "https://api.canva.com/rest/v1/users/me"
CANVA_PROFILE_URL = "https://api.canva.com/rest/v1/users/me/profile"
DEFAULT_CANVA_REDIRECT_URI = "http://127.0.0.1:8765/connectors/canva/callback"
DEFAULT_EMAIL_REDIRECT_URI = "http://127.0.0.1:8765/connectors/email/callback"
DEFAULT_CANVA_SCOPES = ("profile:read", "design:content:write", "design:meta:read")

EMAIL_PROVIDERS = {
    "gmail": "Gmail",
    "generic": "Generic mailto/draft handoff",
}

EMAIL_MODES = {
    "draft_only": "Draft only",
    "disabled": "Disabled",
}


class ConnectorSettingsService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.path = user_dir(self.root) / "connectors.yaml"

    def load(self) -> dict[str, Any]:
        data = self._read_store()
        settings = _default_settings()
        for key in ("canva", "email"):
            if isinstance(data.get(key), dict):
                settings[key].update(data[key])
        settings["canva"] = _normalize_canva(settings["canva"], self._canva_oauth_config())
        settings["email"] = _normalize_email(settings["email"])
        settings["claude"] = self._claude_status()
        settings["email_provider_options"] = [
            {"value": value, "label": label} for value, label in EMAIL_PROVIDERS.items()
        ]
        settings["email_mode_options"] = [{"value": value, "label": label} for value, label in EMAIL_MODES.items()]
        return settings

    def save_from_form(self, form: Any) -> dict[str, Any]:
        current = self._read_store()
        canva = _dict(current.get("canva"))
        canva.update(
            {
                "enabled": _truthy(form.get("canva_enabled")) or bool(canva.get("access_token")),
                "preferred_output": str(
                    form.get("canva_preferred_output") or canva.get("preferred_output") or "doc"
                ).strip(),
                "mcp_server_url": CANVA_MCP_SERVER_URL,
            }
        )
        settings = {
            "canva": canva,
            "email": _normalize_email(
                {
                    "enabled": _truthy(form.get("email_enabled")),
                    "provider": str(form.get("email_provider") or "gmail").strip(),
                    "account_email": str(form.get("email_account_email") or "").strip(),
                    "mode": str(form.get("email_mode") or "draft_only").strip(),
                }
            ),
        }
        write_yaml(self.path, settings)
        return self.load()

    def canva_authorization_url(self) -> str:
        config = self._canva_oauth_config()
        if not config["client_id"] or not config["client_secret"]:
            raise ValueError("Canva sign-in is not configured for this local app yet.")
        state = secrets.token_urlsafe(48)
        code_verifier = secrets.token_urlsafe(96)[:128]
        code_challenge = _code_challenge(code_verifier)
        scopes = config["scopes"]
        store = self._read_store()
        canva = _dict(store.get("canva"))
        canva.update(
            {
                "enabled": True,
                "mcp_server_url": CANVA_MCP_SERVER_URL,
                "oauth_client_id": config["client_id"],
                "oauth_redirect_uri": config["redirect_uri"],
                "oauth_scopes": scopes,
                "pending_oauth": {
                    "state": state,
                    "code_verifier": code_verifier,
                    "redirect_uri": config["redirect_uri"],
                    "scopes": scopes,
                    "created_at": datetime.now(UTC).isoformat(),
                },
            }
        )
        store["canva"] = canva
        write_yaml(self.path, store)
        return (
            CANVA_AUTHORIZATION_URL
            + "?"
            + urlencode(
                {
                    "code_challenge": code_challenge,
                    "code_challenge_method": "S256",
                    "scope": " ".join(scopes),
                    "response_type": "code",
                    "client_id": config["client_id"],
                    "state": state,
                    "redirect_uri": config["redirect_uri"],
                }
            )
        )

    def complete_canva_oauth(self, code: str, state: str) -> dict[str, Any]:
        store = self._read_store()
        canva = _dict(store.get("canva"))
        pending = _dict(canva.get("pending_oauth"))
        if not code:
            raise ValueError("Canva did not return an authorization code.")
        if not state or state != str(pending.get("state") or ""):
            raise ValueError("Canva sign-in state did not match. Please try connecting again.")
        config = self._canva_oauth_config()
        if not config["client_id"] or not config["client_secret"]:
            raise ValueError("Canva sign-in is not configured for this local app yet.")
        try:
            token = self._exchange_canva_code(
                code=code,
                code_verifier=str(pending.get("code_verifier") or ""),
                redirect_uri=str(pending.get("redirect_uri") or config["redirect_uri"]),
                client_id=config["client_id"],
                client_secret=config["client_secret"],
            )
        except requests.RequestException as exc:
            raise ValueError(f"Canva sign-in failed while requesting tokens: {exc}") from exc
        access_token = str(token.get("access_token") or "")
        if not access_token:
            raise ValueError("Canva did not return an access token.")
        try:
            profile = self._fetch_canva_profile(access_token)
        except requests.RequestException as exc:
            raise ValueError(f"Canva sign-in failed while reading your profile: {exc}") from exc
        canva.update(
            {
                "enabled": True,
                "mcp_server_url": CANVA_MCP_SERVER_URL,
                "oauth_client_id": config["client_id"],
                "oauth_redirect_uri": str(pending.get("redirect_uri") or config["redirect_uri"]),
                "oauth_scopes": token.get("scope", " ".join(config["scopes"])),
                "access_token": access_token,
                "refresh_token": str(token.get("refresh_token") or ""),
                "token_type": str(token.get("token_type") or "Bearer"),
                "expires_in": token.get("expires_in"),
                "connected_display_name": str(profile.get("display_name") or ""),
                "connected_user_id": str(profile.get("user_id") or ""),
                "connected_team_id": str(profile.get("team_id") or ""),
                "connected_at": datetime.now(UTC).isoformat(),
                "pending_oauth": {},
            }
        )
        store["canva"] = canva
        write_yaml(self.path, store)
        return self.load()

    def disconnect_canva(self) -> dict[str, Any]:
        store = self._read_store()
        canva = _dict(store.get("canva"))
        for key in [
            "access_token",
            "refresh_token",
            "token_type",
            "expires_in",
            "connected_display_name",
            "connected_user_id",
            "connected_team_id",
            "connected_at",
            "pending_oauth",
        ]:
            canva.pop(key, None)
        canva["enabled"] = False
        store["canva"] = canva
        write_yaml(self.path, store)
        return self.load()

    def _read_store(self) -> dict[str, Any]:
        raw = read_yaml(self.path, {})
        return raw if isinstance(raw, dict) else {}

    def _canva_oauth_config(self) -> dict[str, Any]:
        env = load_env(self.root)
        scopes = tuple(
            item.strip()
            for item in str(env.get("CANVA_SCOPES") or " ".join(DEFAULT_CANVA_SCOPES)).split()
            if item.strip()
        )
        return {
            "client_id": str(env.get("CANVA_CLIENT_ID") or "").strip(),
            "client_secret": str(env.get("CANVA_CLIENT_SECRET") or "").strip(),
            "redirect_uri": str(env.get("CANVA_REDIRECT_URI") or DEFAULT_CANVA_REDIRECT_URI).strip(),
            "scopes": scopes or DEFAULT_CANVA_SCOPES,
        }

    def _exchange_canva_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        client_id: str,
        client_secret: str,
    ) -> dict[str, Any]:
        if not code_verifier:
            raise ValueError("Canva sign-in session is missing. Please try connecting again.")
        response = requests.post(
            CANVA_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": redirect_uri,
            },
            auth=(client_id, client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Canva token response was not a JSON object.")
        return data

    def _fetch_canva_profile(self, access_token: str) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        profile_response = requests.get(CANVA_PROFILE_URL, headers=headers, timeout=20)
        profile_response.raise_for_status()
        profile_data = profile_response.json()
        user_response = requests.get(CANVA_USER_URL, headers=headers, timeout=20)
        user_response.raise_for_status()
        user_data = user_response.json()
        profile = _dict(profile_data.get("profile") if isinstance(profile_data, dict) else {})
        team_user = _dict(user_data.get("team_user") if isinstance(user_data, dict) else {})
        return {
            "display_name": str(profile.get("display_name") or ""),
            "user_id": str(team_user.get("user_id") or ""),
            "team_id": str(team_user.get("team_id") or ""),
        }

    def _claude_status(self) -> dict[str, Any]:
        env = load_env(self.root)
        api_key = str(env.get("ANTHROPIC_API_KEY") or "").strip()
        configured = bool(api_key and api_key.lower() not in {"your_key_here", "changeme", "placeholder"})
        return {
            "configured": configured,
            "model": str(env.get("CLAUDE_MODEL") or DEFAULT_CLAUDE_MODEL),
            "use_by_default": str(env.get("CLAUDE_USE_BY_DEFAULT") or "").lower() == "true",
        }


def _default_settings() -> dict[str, Any]:
    return deepcopy(
        {
            "canva": {
                "enabled": False,
                "mcp_server_url": CANVA_MCP_SERVER_URL,
                "oauth_client_id": "",
                "oauth_redirect_uri": DEFAULT_CANVA_REDIRECT_URI,
                "connected_display_name": "",
                "connected_user_id": "",
                "connected_team_id": "",
                "preferred_output": "doc",
                "oauth_status": "not_connected",
                "oauth_ready": False,
            },
            "email": {
                "enabled": False,
                "provider": "gmail",
                "account_email": "",
                "oauth_client_id": "",
                "oauth_redirect_uri": DEFAULT_EMAIL_REDIRECT_URI,
                "mode": "draft_only",
                "sender_name": "",
                "sending_enabled": False,
                "oauth_status": "not_connected",
            },
        }
    )


def _normalize_canva(data: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    url = _https_url(str(data.get("mcp_server_url") or CANVA_MCP_SERVER_URL).strip(), CANVA_MCP_SERVER_URL)
    preferred_output = str(data.get("preferred_output") or "doc").strip()
    if preferred_output not in {"doc", "design_import"}:
        preferred_output = "doc"
    display_name = str(data.get("connected_display_name") or "").strip()
    user_id = str(data.get("connected_user_id") or "").strip()
    connected = bool(data.get("access_token") and (display_name or user_id))
    return {
        "enabled": bool(data.get("enabled") or connected),
        "mcp_server_url": url,
        "oauth_client_id": str(data.get("oauth_client_id") or config.get("client_id") or "").strip(),
        "oauth_redirect_uri": str(
            data.get("oauth_redirect_uri") or config.get("redirect_uri") or DEFAULT_CANVA_REDIRECT_URI
        ).strip(),
        "connected_display_name": display_name,
        "connected_user_id": user_id,
        "connected_team_id": str(data.get("connected_team_id") or "").strip(),
        "connected_at": str(data.get("connected_at") or "").strip(),
        "preferred_output": preferred_output,
        "oauth_status": "connected" if connected else "not_connected",
        "oauth_ready": bool(config.get("client_id") and config.get("client_secret")),
        "oauth_scopes": data.get("oauth_scopes") or list(config.get("scopes") or DEFAULT_CANVA_SCOPES),
    }


def _normalize_email(data: dict[str, Any]) -> dict[str, Any]:
    provider = str(data.get("provider") or "gmail").strip()
    if provider not in EMAIL_PROVIDERS:
        provider = "gmail"
    mode = str(data.get("mode") or "draft_only").strip()
    if mode not in EMAIL_MODES:
        mode = "draft_only"
    account_email = str(data.get("account_email") or "").strip()
    return {
        "enabled": bool(data.get("enabled")),
        "provider": provider,
        "account_email": account_email,
        "oauth_redirect_uri": DEFAULT_EMAIL_REDIRECT_URI,
        "mode": mode,
        "sending_enabled": False,
        "oauth_status": "configured" if account_email else "not_configured",
    }


def _https_url(value: str, default: str) -> str:
    value = value or default
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Canva MCP server URL must be an https URL.")
    return value


def _truthy(value: Any) -> bool:
    return str(value or "").lower() in {"1", "true", "on", "yes"}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
