from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from job_agent.config import ROOT
from job_agent.llm import ExternalAgentService, LlmRequest, LlmService
from job_agent.prompt_context import (
    FIELD_CONTEXTS,
    EditContextPreference,
    EditContextPreferenceStore,
    PromptContextProvider,
)

from .package_index_service import PackageIndexService


@dataclass
class AiEditRequest:
    field_id: str
    button_id: str
    current_text: str
    user_instruction: str
    selected_blocks: list[str]
    disabled_blocks: list[str]
    job_id: str = ""
    run_id: str = ""


class AiEditService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.provider = PromptContextProvider(root)
        self.preferences = EditContextPreferenceStore(root)
        self.packages = PackageIndexService(root)
        self.llm = LlmService(root)

    def context_payload(self, field_id: str, button_id: str, job_id: str = "", run_id: str = "") -> dict:
        package = self.packages.find_package(job_id, run_id) if job_id else None
        files = self.packages.read_package_files(package) if package else {}
        blocks = self.provider.available_blocks(package, files)
        defaults = self.provider.default_blocks_for_field(field_id)
        pref = self.preferences.get(button_id, defaults)
        return {
            "field_id": field_id,
            "button_id": button_id,
            "field_context": FIELD_CONTEXTS.get(field_id, "We are editing a text field in Job Agent."),
            "blocks": [block.__dict__ for block in blocks.values()],
            "selected_blocks": pref.selected_blocks,
            "disabled_blocks": pref.disabled_blocks,
        }

    def generate(self, request: AiEditRequest) -> dict:
        prompt = self._build_prompt_and_save_preferences(request)
        completion = self.llm.complete(
            prompt,
            max_tokens=2200,
            purpose="ai_edit",
            run_id=request.run_id,
            associated_job_id=request.job_id,
        )
        return {"revised_text": completion.text, "prompt": prompt, "model": completion.model}

    def prepare_external(self, request: AiEditRequest) -> dict:
        prompt = self._build_prompt_and_save_preferences(request)
        interaction = ExternalAgentService(self.root).prepare(
            LlmRequest(
                prompt=prompt,
                max_tokens=2200,
                purpose="ai_edit",
                run_id=request.run_id,
                associated_job_id=request.job_id,
            ),
            title="Edit field with external agent",
            instructions="Paste this prompt into an external agent. Paste back only the revised replacement text.",
            metadata={
                "field_id": request.field_id,
                "button_id": request.button_id,
                "job_id": request.job_id,
                "run_id": request.run_id,
            },
        )
        return {"prompt": prompt, "interaction_id": interaction.interaction_id, "model": "external-agent"}

    def _build_prompt_and_save_preferences(self, request: AiEditRequest) -> str:
        package = self.packages.find_package(request.job_id, request.run_id) if request.job_id else None
        files = self.packages.read_package_files(package) if package else {}
        prompt = self.provider.build_prompt(
            field_id=request.field_id,
            current_text=request.current_text,
            user_instruction=request.user_instruction,
            selected_blocks=request.selected_blocks,
            disabled_blocks=request.disabled_blocks,
            job_package=package,
            job_files=files,
        )
        self.preferences.save(
            EditContextPreference(
                button_id=request.button_id,
                selected_blocks=request.selected_blocks,
                disabled_blocks=request.disabled_blocks,
            )
        )
        return prompt
