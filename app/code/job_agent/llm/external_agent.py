from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from job_agent.config import ROOT
from job_agent.io.json_store import read_json, write_json
from job_agent.llm.catalog import DEFAULT_LLM_PROVIDER
from job_agent.llm.gateway import LlmCompletion, LlmRequest
from job_agent.paths import output_dir

EXTERNAL_AGENT_PROVIDER = "external_agent"
EXTERNAL_AGENT_MODEL = "external-agent"


@dataclass(frozen=True)
class ExternalAgentInteraction:
    interaction_id: str
    created_at: str
    title: str
    instructions: str
    request: LlmRequest
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    completed_at: str = ""
    response_text: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "interaction_id": self.interaction_id,
            "created_at": self.created_at,
            "title": self.title,
            "instructions": self.instructions,
            "prompt": self.request.prompt,
            "max_tokens": self.request.max_tokens,
            "purpose": self.request.purpose,
            "run_id": self.request.run_id,
            "associated_job_id": self.request.associated_job_id,
            "metadata": self.metadata,
            "status": self.status,
            "completed_at": self.completed_at,
        }


class ExternalAgentService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.interactions_dir = output_dir(root) / "external-agent-interactions"

    def prepare(
        self,
        request: LlmRequest,
        *,
        title: str = "",
        instructions: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ExternalAgentInteraction:
        interaction = ExternalAgentInteraction(
            interaction_id=uuid4().hex,
            created_at=_now(),
            title=title or _title_for_purpose(request.purpose),
            instructions=instructions or "Paste the prompt into an external agent, then paste its response back here.",
            request=request,
            metadata=metadata or {},
        )
        self._write(interaction)
        return interaction

    def load(self, interaction_id: str) -> ExternalAgentInteraction:
        path = self._path(interaction_id)
        if not path.exists():
            raise KeyError(f"External-agent interaction not found: {interaction_id}")
        data = read_json(path, {}, strict=True)
        request_data = data.get("request") if isinstance(data.get("request"), dict) else {}
        request = LlmRequest(
            prompt=str(request_data.get("prompt") or ""),
            max_tokens=int(request_data.get("max_tokens") or 0),
            purpose=str(request_data.get("purpose") or ""),
            run_id=str(request_data.get("run_id") or ""),
            associated_job_id=str(request_data.get("associated_job_id") or ""),
        )
        return ExternalAgentInteraction(
            interaction_id=str(data.get("interaction_id") or interaction_id),
            created_at=str(data.get("created_at") or ""),
            title=str(data.get("title") or ""),
            instructions=str(data.get("instructions") or ""),
            request=request,
            metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
            status=str(data.get("status") or "pending"),
            completed_at=str(data.get("completed_at") or ""),
            response_text=str(data.get("response_text") or ""),
        )

    def complete(self, interaction_id: str, response_text: str) -> LlmCompletion:
        interaction = self.load(interaction_id)
        text = response_text.strip()
        if not text:
            raise ValueError("Paste the external agent response before applying it.")
        completed = ExternalAgentInteraction(
            interaction_id=interaction.interaction_id,
            created_at=interaction.created_at,
            title=interaction.title,
            instructions=interaction.instructions,
            request=interaction.request,
            metadata=interaction.metadata,
            status="completed",
            completed_at=_now(),
            response_text=text,
        )
        self._write(completed)
        return LlmCompletion(text=text, model=EXTERNAL_AGENT_MODEL, provider=EXTERNAL_AGENT_PROVIDER)

    def _write(self, interaction: ExternalAgentInteraction) -> None:
        payload = asdict(interaction)
        payload["request"] = asdict(interaction.request)
        write_json(self._path(interaction.interaction_id), payload)

    def _path(self, interaction_id: str) -> Path:
        if not interaction_id or any(part in interaction_id for part in ("/", "\\", "..")):
            raise KeyError(f"Invalid external-agent interaction id: {interaction_id}")
        self.interactions_dir.mkdir(parents=True, exist_ok=True)
        return self.interactions_dir / f"{interaction_id}.json"


def _title_for_purpose(purpose: str) -> str:
    labels = {
        "ai_edit": "Edit with external agent",
        "application_generation": "Draft application with external agent",
        "profile_auto_configuration": "Draft profile settings with external agent",
        "recipe_suggestion": "Generate reading plan with external agent",
    }
    return labels.get(purpose, f"External agent: {purpose or DEFAULT_LLM_PROVIDER}")


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
