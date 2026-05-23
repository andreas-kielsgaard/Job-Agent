from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

from job_agent.config import ROOT
from job_agent.token_usage import TokenUsageStore, token_record_from_anthropic_response

DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-0"


@dataclass
class LlmCompletion:
    text: str
    model: str


class LlmService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root

    def config_value(self, key: str, default: str = "") -> str:
        # The local setup page writes .env while the web app is already running, so .env is read fresh for each
        # call and intentionally wins over process env. Process env remains a fallback for CLI/automation usage.
        env_file_values = dotenv_values(self.root / ".env")
        value = env_file_values.get(key)
        if value:
            return str(value)
        return os.getenv(key) or default

    def model_name(self) -> str:
        return self.config_value("CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL)

    def api_key(self) -> str:
        return self.config_value("ANTHROPIC_API_KEY")

    def is_configured(self) -> bool:
        api_key = self.api_key()
        return bool(api_key and not api_key.startswith("replace_with"))

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int,
        purpose: str,
        run_id: str = "",
        associated_job_id: str = "",
    ) -> LlmCompletion:
        api_key = self.api_key()
        model = self.model_name()
        if not self.is_configured():
            raise RuntimeError("ANTHROPIC_API_KEY is missing or placeholder.")

        from anthropic import Anthropic

        client = Anthropic(api_key=api_key)
        attempted_models: list[str] = []
        last_model_not_found: Exception | None = None
        for candidate_model in _model_candidates(model):
            attempted_models.append(candidate_model)
            try:
                response = client.messages.create(
                    model=candidate_model,
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
                model = candidate_model
                break
            except Exception as exc:
                if not _looks_like_model_not_found(exc):
                    raise
                last_model_not_found = exc
        else:
            attempted = ", ".join(f"`{candidate}`" for candidate in attempted_models)
            raise RuntimeError(
                f"Selected Claude model was not available. Tried {attempted}. "
                f"Open Setup and choose Balanced default (`{DEFAULT_CLAUDE_MODEL}`), "
                "or update CLAUDE_MODEL in .env."
            ) from last_model_not_found
        usage_run_id = run_id or "manual"
        if usage_run_id:
            TokenUsageStore(self.root).append(
                token_record_from_anthropic_response(
                    run_id=usage_run_id,
                    purpose=purpose,
                    model=model,
                    associated_job_id=associated_job_id,
                    response=response,
                )
            )
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        return LlmCompletion(text=text, model=model)


def _looks_like_model_not_found(exc: Exception) -> bool:
    text = str(exc).lower()
    return "not_found_error" in text or ("model" in text and "not found" in text)


def _model_candidates(model: str) -> list[str]:
    model = model.strip()
    candidates = [model] if model else [DEFAULT_CLAUDE_MODEL]
    alias = _fallback_model_alias(model)
    if alias and alias not in candidates:
        candidates.append(alias)
    return candidates


def _fallback_model_alias(model: str) -> str:
    if not model or model.endswith("-latest") or model.endswith("-0"):
        return ""
    versioned_aliases = [
        (r"^claude-3-5-haiku-\d{8}$", "claude-3-5-haiku-latest"),
        (r"^claude-3-5-sonnet-\d{8}$", "claude-3-5-sonnet-latest"),
        (r"^claude-3-7-sonnet-\d{8}$", "claude-3-7-sonnet-latest"),
        (r"^claude-sonnet-4-\d{8}$", "claude-sonnet-4-0"),
        (r"^claude-opus-4-\d{8}$", "claude-opus-4-0"),
    ]
    for pattern, alias in versioned_aliases:
        if re.fullmatch(pattern, model):
            return alias
    return ""
