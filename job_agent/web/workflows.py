from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from job_agent.config import ROOT
from job_agent.profile_contract import build_profile_contract
from job_agent.run_store import RunOptions, RunStore
from job_agent.services.approved_recipe_adoption_service import ApprovedRecipeAdoptionService
from job_agent.services.cv_profile_draft_service import CvProfileDraftService
from job_agent.services.cv_reference_service import CvReferenceService
from job_agent.services.recipe_calibration_service import capture_recipe_calibration
from job_agent.services.recipe_candidate_approval_service import RecipeCandidateApprovalService
from job_agent.services.recipe_candidate_policy import candidate_is_reviewable
from job_agent.services.recipe_candidate_service import RecipeCandidateStore
from job_agent.services.recipe_generation_run_service import RecipeGenerationRunService
from job_agent.services.recipe_generation_status_service import RecipeGenerationStatusService
from job_agent.services.setup_guide_service import SetupGuideService
from job_agent.services.setup_service import SetupService
from job_agent.web.source_workflow import SourceWorkflowHandler
from job_agent.web.view_models.dashboard import build_dashboard_view
from job_agent.web.view_models.runs import build_run_detail_view, build_run_list_view
from job_agent.web.view_models.setup import build_setup_view


@dataclass(frozen=True)
class WorkflowArea:
    key: str
    label: str
    owner: str
    state_inputs: tuple[str, ...]
    handoffs: tuple[str, ...]


class AppWorkflowHandler:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.source = SourceWorkflowHandler(self.root)
        self.recipe = RecipeWorkflowHandler(self.root, self.source)
        self.executor = ExecutorWorkflowHandler(self.root)
        self.profile = ProfileWorkflowHandler(self.root)
        self.guide = SetupGuideWorkflowHandler(self.root)

    def map(self) -> dict[str, WorkflowArea]:
        return {
            "profile": WorkflowArea(
                key="profile",
                label="Profile setup and evidence",
                owner="ProfileWorkflowHandler",
                state_inputs=(
                    "profile/*.yaml",
                    "profile/canonical-cv.md",
                    "profile/writing-style.md",
                    "profile/files/reference-cv.*",
                    "profile draft task state",
                ),
                handoffs=("executor", "recipe"),
            ),
            "source": WorkflowArea(
                key="source",
                label="Source setup and verification",
                owner="SourceWorkflowHandler",
                state_inputs=(
                    "source registry",
                    "execution projection",
                    "recipe generation status",
                    "source readiness",
                    "source session",
                    "listing index",
                    "detail review coverage",
                ),
                handoffs=("recipe", "executor"),
            ),
            "recipe": WorkflowArea(
                key="recipe",
                label="Recipe generation and correction",
                owner="RecipeWorkflowHandler",
                state_inputs=(
                    "source workflow diagnosis",
                    "recipe calibration artifacts",
                    "recipe candidates",
                    "recipe generation runs",
                ),
                handoffs=("source",),
            ),
            "executor": WorkflowArea(
                key="executor",
                label="Runs and background execution",
                owner="ExecutorWorkflowHandler",
                state_inputs=(
                    "run store",
                    "runtime work status",
                    "source workflow readiness",
                    "profile contract",
                ),
                handoffs=("source", "profile"),
            ),
        }


class ExecutorWorkflowHandler:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.runs = RunStore(self.root)

    def dashboard_view(self) -> dict[str, Any]:
        return build_dashboard_view(self.root)

    def run_list_view(self, view: str) -> dict[str, Any]:
        return build_run_list_view(view, self.root)

    def run_detail_view(
        self,
        run_id: str,
        category: str = "",
        app_status: str = "",
        source: str = "",
        *,
        only_unreviewed: bool = False,
        ai_prioritized: bool = False,
        materials_missing: bool = False,
        match_group: str = "",
    ) -> dict[str, Any]:
        return build_run_detail_view(
            run_id,
            category,
            app_status,
            source,
            self.root,
            only_unreviewed=only_unreviewed,
            ai_prioritized=ai_prioritized,
            materials_missing=materials_missing,
            match_group=match_group,
        )

    def launch_daily_run(self, runtime, options: RunOptions):
        return runtime.launch_daily_run(options)

    def apply_run_action(self, run_id: str, action: str) -> None:
        actions = {
            "archive": self.runs.archive,
            "delete": self.runs.soft_delete,
            "restore": self.runs.restore,
        }
        if action not in actions:
            raise ValueError(f"Unsupported run action: {action}")
        actions[action](run_id)

    def apply_bulk_run_action(self, run_ids: list[str], action: str) -> None:
        for run_id in run_ids:
            try:
                self.apply_run_action(run_id, action)
            except KeyError:
                continue

    def run_status_payload(self, run_id: str) -> dict[str, Any]:
        record = self.runs.get(run_id)
        if not record:
            raise KeyError(run_id)
        events = self.runs.read_events(run_id, limit=20)
        latest = events[-1] if events else {}
        view = build_run_detail_view(run_id, root=self.root)
        return {
            "run": record.__dict__,
            "latest_event": latest,
            "recent_events": events,
            "run_overview": view["run_overview"],
            "run_progress": view["run_progress"],
            "source_progress": {
                "items": view["source_progress"],
                "summary": view["source_progress_summary"],
            },
            "match_highlights": view["match_highlights"],
            "activity": view["activity"],
            "packages": view["packages"],
        }

    def run_log_context(self, run_id: str) -> dict[str, Any]:
        record = self.runs.get(run_id)
        if not record:
            raise KeyError(run_id)
        return {
            "title": f"Run Log - {record.started_at[:10] or record.run_id}",
            "run": record,
            "log_text": self.run_log_text(run_id),
        }

    def run_log_text(self, run_id: str) -> str:
        record = self.runs.get(run_id)
        if not record:
            raise KeyError(run_id)
        path = Path(record.run_log_path)
        return path.read_text(encoding="utf-8") if path.exists() else ""


class RecipeWorkflowHandler:
    def __init__(self, root: Path = ROOT, source: SourceWorkflowHandler | None = None) -> None:
        self.root = Path(root)
        self.source = source or SourceWorkflowHandler(self.root)
        self.status_service = RecipeGenerationStatusService(self.root)
        self.run_service = RecipeGenerationRunService(self.root)
        self.candidates = RecipeCandidateStore(self.root)
        self.approvals = RecipeCandidateApprovalService(self.root)
        self.adoptions = ApprovedRecipeAdoptionService(self.root)

    def status_for_source(self, source_id: str):
        return self.status_service.build_for_source(source_id)

    def current_source_test_insight(self, source_id: str) -> dict[str, Any]:
        state = self.source.build(source_id)
        if not state:
            raise ValueError(f"Source not found: {source_id}")
        return state.source_test_insight

    def generation_clues_for_source(self, source_id: str) -> dict[str, Any]:
        insight = self.current_source_test_insight(source_id)
        return dict(insight.get("generation_clues") or insight)

    def start_from_source_capture(self, source_id: str, **kwargs):
        return self.run_service.start_from_source_capture(source_id, **kwargs)

    def start_from_artifact(self, source_id: str, **kwargs):
        return self.run_service.start(source_id, **kwargs)

    def load_run(self, run_id: str) -> dict[str, Any]:
        return self.run_service.load(run_id)

    def capture_calibration(
        self,
        source_id: str,
        *,
        rendered: bool | None,
        capture_detail: bool,
        max_candidates: int,
    ):
        source = self.source.require_source(source_id)
        self.source.require_not_archived(source)
        if not source.url:
            raise ValueError("Save a source URL before capturing calibration evidence.")
        session_status = self.source.source_session_status(source)
        session_state_path = self.root / session_status.storage_state_path if session_status.usable else None
        return capture_recipe_calibration(
            source.url,
            recipe_path=source.recipe_path or None,
            rendered=rendered,
            root=self.root,
            max_candidates=max(5, min(max_candidates, 50)),
            capture_detail=capture_detail,
            session_state_path=session_state_path,
            source_session_scope=session_status.session_scope if session_status.usable else "",
        )

    def load_source_run(self, source_id: str, run_id: str) -> dict[str, Any]:
        source = self.source.require_source(source_id)
        run = self.load_run(run_id)
        if run.get("source_id") != source.id:
            raise ValueError("Recipe generation run does not belong to this source.")
        return run

    def candidate_detail_context(self, candidate_id: str, *, source_id: str = "") -> dict[str, Any]:
        candidate = self.candidates.load_candidate(candidate_id)
        source = self.source.require_source(source_id) if source_id else self.source.source_for_candidate(candidate)
        title_target = source.name if source else candidate.candidate_id
        return {
            "title": f"Recipe Candidate - {title_target}",
            "candidate": candidate,
            "source": source,
            "approval_recipe_path": self.approvals.suggested_recipe_path(candidate, source),
            "candidate_can_be_used": candidate_is_reviewable(candidate),
        }

    def reject_candidate(self, candidate_id: str, *, reason: str = ""):
        return self.candidates.reject_candidate(candidate_id, reason=reason)

    def adopt_candidate(
        self,
        candidate_id: str,
        source_id: str,
        *,
        prepare_disabled_execution_entry: bool = False,
    ):
        return self.adoptions.adopt(
            candidate_id,
            source_id,
            prepare_disabled_execution_entry=prepare_disabled_execution_entry,
        )

    def approve_candidate(
        self,
        candidate_id: str,
        recipe_path: str,
        *,
        source_id: str = "",
        overwrite: bool = False,
    ):
        source = self.source.require_source(source_id) if source_id else None
        return self.approvals.approve(
            candidate_id,
            recipe_path,
            source_id=source_id,
            overwrite=overwrite,
            base_url=source.url if source else "",
        )


class ProfileWorkflowHandler:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.setup = SetupService(self.root)
        self.drafts = CvProfileDraftService(self.root)
        self.references = CvReferenceService(self.root)

    def setup_view(self) -> dict[str, Any]:
        return build_setup_view(self.root)

    def active_draft(self) -> dict[str, Any]:
        return self.drafts.active_draft()

    def cv_reference(self) -> dict[str, Any]:
        return self.references.get_cv_reference()

    def contract(self) -> dict[str, Any]:
        return build_profile_contract(self.root, self.cv_reference())

    def save_env_settings(self, anthropic_api_key: str, claude_model: str, claude_use_by_default: bool) -> None:
        self.setup.save_env_settings(anthropic_api_key, claude_model, claude_use_by_default)

    def save_contact(self, data: dict[str, Any]) -> None:
        self.setup.save_contact(data)

    def save_preferences(self, **kwargs: Any) -> None:
        self.setup.save_preferences(**kwargs)

    def save_run_inclusion(self, minimum_digest_score: int) -> None:
        self.setup.save_run_inclusion(minimum_digest_score)

    def save_runtime_settings(self, max_parallel_sources: int) -> None:
        self.setup.save_runtime_settings(max_parallel_sources)

    def save_match_engine_settings_from_form(self, form) -> None:
        self.setup.save_match_engine_settings_from_form(form)

    def save_skill_matrix_from_form(self, form) -> None:
        self.setup.save_skill_matrix_from_form(form)

    def save_case_studies_from_form(self, form) -> None:
        self.setup.save_case_studies_from_form(form)

    def save_writing_reference(self, canonical_cv: str | None = None, writing_style: str | None = None) -> None:
        self.setup.save_writing_reference(canonical_cv, writing_style)

    def save_application_examples_from_form(self, form) -> None:
        self.setup.save_application_examples_from_form(form)

    def save_ai_policy_from_form(self, form) -> None:
        self.setup.save_ai_policy_from_form(form)

    def save_setup_file(self, file_key: str, content: str) -> None:
        self.setup.save_setup_file(file_key, content)

    def store_reference_cv(self, filename: str, content: bytes, extract_to_canonical: bool) -> str:
        self.references.store_reference_cv(filename, content, extract_to_canonical)
        return self.cv_reference().get("extracted_text", "")

    def auto_config_targets_from_form(self, form) -> list[str]:
        return self.setup.auto_config_targets_from_form(form)

    def auto_configure_profile_from_cv(self, cv_text: str, targets: list[str], *, progress_callback=None):
        return self.setup.auto_configure_profile_from_cv(cv_text, targets, progress_callback=progress_callback)

    def draft_profile_auto_configuration_from_cv(self, cv_text: str, targets: list[str], *, progress_callback=None):
        return self.setup.draft_profile_auto_configuration_from_cv(
            cv_text,
            targets,
            progress_callback=progress_callback,
        )

    def apply_profile_auto_configuration(self, data: dict[str, Any], targets: list[str]) -> dict[str, Any]:
        return self.setup.apply_profile_auto_configuration(data, targets)

    def save_draft(self, draft: dict[str, Any], *, source_label: str, task_id: str = "") -> dict[str, Any]:
        return self.drafts.save_draft(draft, source_label=source_label, task_id=task_id)

    def clear_profile_draft_task(self) -> None:
        self.drafts.clear_task()

    def clear_active_draft(self, draft_id: str) -> bool:
        return self.drafts.clear_active_draft(draft_id)


class SetupGuideWorkflowHandler:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.service = SetupGuideService(self.root)

    def context(self, *, current_path: str = "") -> dict[str, Any]:
        return self.service.build_context(current_path=current_path)

    def dismiss_guide(self) -> None:
        self.service.dismiss_guide()

    def dismiss_step(self, step_id: str) -> None:
        self.service.dismiss_step(step_id)

    def reset(self) -> None:
        self.service.reset()
