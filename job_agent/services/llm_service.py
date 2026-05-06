from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from job_agent.config import ROOT
from job_agent.token_usage import TokenUsageStore, token_record_from_anthropic_response


@dataclass
class LlmCompletion:
    text: str
    model: str


class LlmService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int,
        purpose: str,
        run_id: str = "",
        associated_job_id: str = "",
    ) -> LlmCompletion:
        load_dotenv(self.root / ".env")
        api_key = os.getenv("ANTHROPIC_API_KEY")
        model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-0")
        if not api_key or api_key.startswith("replace_with"):
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
