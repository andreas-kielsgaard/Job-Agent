from __future__ import annotations

import re
from dataclasses import asdict, dataclass

DEFAULT_LLM_PROVIDER = "anthropic"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"
CURRENT_OPUS_MODEL = "claude-opus-4-8"
CURRENT_HAIKU_MODEL = "claude-haiku-4-5"

LEGACY_MODEL_REPLACEMENTS = {
    "claude-sonnet-4-0": DEFAULT_CLAUDE_MODEL,
    "claude-sonnet-4-20250514": DEFAULT_CLAUDE_MODEL,
    "claude-opus-4-0": CURRENT_OPUS_MODEL,
    "claude-3-5-haiku-latest": CURRENT_HAIKU_MODEL,
}


@dataclass(frozen=True)
class LlmModelOption:
    provider: str
    label: str
    value: str
    quality: str
    speed: str
    price: str
    help: str

    def as_template_dict(self) -> dict[str, str]:
        return asdict(self)


MODEL_OPTIONS = [
    LlmModelOption(
        provider="anthropic",
        label="Balanced default",
        value=DEFAULT_CLAUDE_MODEL,
        quality="High performance",
        speed="Medium",
        price="Medium",
        help="Current Sonnet release. Good default balance of quality, cost, and availability.",
    ),
    LlmModelOption(
        provider="anthropic",
        label="Highest performance",
        value=CURRENT_OPUS_MODEL,
        quality="Highest performance",
        speed="Moderate",
        price="High",
        help="Most capable listed model, usually higher cost and slower.",
    ),
    LlmModelOption(
        provider="anthropic",
        label="Cheapest and fastest",
        value=CURRENT_HAIKU_MODEL,
        quality="Near-frontier performance",
        speed="High",
        price="Low",
        help="Current Haiku release. Fast and cheaper, but weaker than Sonnet for nuanced writing.",
    ),
]


def model_options(provider: str = DEFAULT_LLM_PROVIDER) -> list[dict[str, str]]:
    return [option.as_template_dict() for option in MODEL_OPTIONS if option.provider == provider]


def default_model(provider: str = DEFAULT_LLM_PROVIDER) -> str:
    if provider == "anthropic":
        return DEFAULT_CLAUDE_MODEL
    raise ValueError(f"Unsupported LLM provider: {provider}")


def normalize_model(model: str, provider: str = DEFAULT_LLM_PROVIDER) -> str:
    model = model.strip()
    if not model:
        return default_model(provider)
    if provider == "anthropic":
        return LEGACY_MODEL_REPLACEMENTS.get(model, model)
    raise ValueError(f"Unsupported LLM provider: {provider}")


def model_candidates(model: str, provider: str = DEFAULT_LLM_PROVIDER) -> list[str]:
    model = model.strip()
    candidates = [normalize_model(model, provider)]
    if provider == "anthropic":
        for fallback in [_anthropic_fallback_model(model), DEFAULT_CLAUDE_MODEL]:
            if fallback and fallback not in candidates:
                candidates.append(fallback)
        return candidates
    raise ValueError(f"Unsupported LLM provider: {provider}")


def _anthropic_fallback_model(model: str) -> str:
    if not model:
        return ""
    versioned_aliases = [
        (r"^claude-3-5-haiku-\d{8}$", CURRENT_HAIKU_MODEL),
        (r"^claude-3-5-sonnet-\d{8}$", "claude-3-5-sonnet-latest"),
        (r"^claude-3-7-sonnet-\d{8}$", "claude-3-7-sonnet-latest"),
        (r"^claude-sonnet-4-\d{8}$", DEFAULT_CLAUDE_MODEL),
        (r"^claude-opus-4(-1)?-\d{8}$", CURRENT_OPUS_MODEL),
    ]
    for pattern, alias in versioned_aliases:
        if re.fullmatch(pattern, model):
            return alias
    return ""
