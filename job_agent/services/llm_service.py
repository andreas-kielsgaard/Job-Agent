from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

from job_agent.config import ROOT
from job_agent.token_usage import TokenUsageStore, token_record_from_anthropic_response


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
        return self.config_value("CLAUDE_MODEL", "claude-sonnet-4-0")

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
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
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
