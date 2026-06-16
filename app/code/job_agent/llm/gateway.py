from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from job_agent.config import ROOT
from job_agent.env import load_env
from job_agent.llm.catalog import DEFAULT_LLM_PROVIDER, default_model, model_candidates, model_options, normalize_model
from job_agent.token_usage import TokenUsageStore, token_record_from_anthropic_response


@dataclass(frozen=True)
class LlmRequest:
    prompt: str
    max_tokens: int
    purpose: str
    run_id: str = ""
    associated_job_id: str = ""
    model: str = ""


@dataclass(frozen=True)
class LlmCompletion:
    text: str
    model: str
    provider: str = DEFAULT_LLM_PROVIDER


class LlmProvider(Protocol):
    provider_id: str

    def api_key(self, config: LlmConfig) -> str:
        raise NotImplementedError

    def is_configured(self, config: LlmConfig) -> bool:
        raise NotImplementedError

    def complete(self, request: LlmRequest, config: LlmConfig) -> tuple[LlmCompletion, Any]:
        raise NotImplementedError


class LlmConfig:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root

    def value(self, key: str, default: str = "") -> str:
        # The local setup page writes .env while the web app is already running, so .env is read fresh for each
        # call and intentionally wins over process env. Process env remains a fallback for CLI/automation usage.
        env_file_values = load_env(self.root)
        value = env_file_values.get(key)
        if value:
            return str(value)
        return os.getenv(key) or default

    def provider_name(self) -> str:
        return self.value("LLM_PROVIDER", DEFAULT_LLM_PROVIDER)

    def model_name(self, provider: str | None = None) -> str:
        provider = provider or self.provider_name()
        env_key = "CLAUDE_MODEL" if provider == "anthropic" else f"{provider.upper()}_MODEL"
        return self.value(env_key, default_model(provider))


class AnthropicProvider:
    provider_id = "anthropic"

    def api_key(self, config: LlmConfig) -> str:
        return config.value("ANTHROPIC_API_KEY")

    def is_configured(self, config: LlmConfig) -> bool:
        api_key = self.api_key(config)
        return bool(api_key and not api_key.startswith("replace_with"))

    def complete(self, request: LlmRequest, config: LlmConfig) -> tuple[LlmCompletion, Any]:
        if not self.is_configured(config):
            raise RuntimeError("ANTHROPIC_API_KEY is missing or placeholder.")

        from anthropic import Anthropic

        client = Anthropic(api_key=self.api_key(config))
        configured_model = request.model or config.model_name(self.provider_id)
        attempted_models: list[str] = []
        last_model_not_found: Exception | None = None
        for candidate_model in model_candidates(configured_model, self.provider_id):
            attempted_models.append(candidate_model)
            try:
                response = client.messages.create(
                    model=candidate_model,
                    max_tokens=request.max_tokens,
                    messages=[{"role": "user", "content": request.prompt}],
                )
                text = "".join(block.text for block in response.content if block.type == "text").strip()
                return LlmCompletion(text=text, model=candidate_model, provider=self.provider_id), response
            except Exception as exc:
                if not _looks_like_model_not_found(exc):
                    raise
                last_model_not_found = exc

        attempted = ", ".join(f"`{candidate}`" for candidate in attempted_models)
        raise RuntimeError(
            f"Selected Claude model was not available. Tried {attempted}. "
            f"Open Setup and choose Balanced default (`{default_model(self.provider_id)}`), "
            "or update CLAUDE_MODEL in .env."
        ) from last_model_not_found


class LlmGateway:
    def __init__(self, root: Path = ROOT, provider: str | None = None) -> None:
        self.root = root
        self.config = LlmConfig(root)
        self.provider_name = provider or self.config.provider_name()
        self.provider = _provider_for(self.provider_name)

    def config_value(self, key: str, default: str = "") -> str:
        return self.config.value(key, default)

    def model_name(self) -> str:
        return self.config.model_name(self.provider_name)

    def normalized_model_name(self) -> str:
        return normalize_model(self.model_name(), self.provider_name)

    def api_key(self) -> str:
        return self.provider.api_key(self.config)

    def is_configured(self) -> bool:
        return self.provider.is_configured(self.config)

    def available_models(self) -> list[dict[str, str]]:
        return model_options(self.provider_name)

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int,
        purpose: str,
        run_id: str = "",
        associated_job_id: str = "",
        model: str = "",
    ) -> LlmCompletion:
        return self.complete_request(
            LlmRequest(
                prompt=prompt,
                max_tokens=max_tokens,
                purpose=purpose,
                run_id=run_id,
                associated_job_id=associated_job_id,
                model=model,
            )
        )

    def complete_request(self, request: LlmRequest) -> LlmCompletion:
        completion, raw_response = self.provider.complete(request, self.config)
        usage_run_id = request.run_id or "manual"
        if usage_run_id and completion.provider == "anthropic":
            TokenUsageStore(self.root).append(
                token_record_from_anthropic_response(
                    run_id=usage_run_id,
                    purpose=request.purpose,
                    model=completion.model,
                    associated_job_id=request.associated_job_id,
                    response=raw_response,
                )
            )
        return completion


LlmService = LlmGateway


def _provider_for(provider: str) -> LlmProvider:
    if provider == "anthropic":
        return AnthropicProvider()
    raise ValueError(f"Unsupported LLM provider: {provider}")


def _looks_like_model_not_found(exc: Exception) -> bool:
    text = str(exc).lower()
    return "not_found_error" in text or ("model" in text and "not found" in text)
