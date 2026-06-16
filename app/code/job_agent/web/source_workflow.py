from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

from job_agent.config import ROOT, load_profile
from job_agent.llm import LlmService
from job_agent.services.execution_source_service import ExecutionSourceService
from job_agent.services.recipe_artifact_service import RecipeArtifactService
from job_agent.services.recipe_candidate_service import RecipeCandidateStore
from job_agent.services.recipe_generation_status_service import RecipeGenerationStatusService
from job_agent.services.recipe_preview_service import explain_recipe
from job_agent.services.recipes.mapping import load_project_job_board_recipe
from job_agent.services.single_source_run_service import SingleSourceRunService
from job_agent.services.source_disqualification_service import SourceDisqualificationService
from job_agent.services.source_execution_readiness_service import (
    SourceExecutionReadiness,
    SourceExecutionReadinessService,
)
from job_agent.services.source_listing_index_service import SourceListingIndexService
from job_agent.services.source_listing_index_store import SourceListingIndexStore, SourceListingIndexSummary
from job_agent.services.source_registry_service import SourceRegistryService
from job_agent.services.source_session_service import SourceSessionService
from job_agent.services.source_suggestion_service import SourceSuggestionService
from job_agent.services.source_test_service import SourceTestService
from job_agent.services.source_url_assessment import assess_source_setup_url
from job_agent.store import JobStore
from job_agent.web.view_models.source_status import (
    build_source_page_status,
    build_source_run_eligibility,
    build_source_setup_steps,
    readiness_has_stale_recipe_test,
)


@dataclass
class SourceWorkflowState:
    source: Any
    execution_entry: dict[str, Any] | None
    artifacts: list[Any]
    recipe_candidates: list[Any]
    generation_status: Any
    readiness: Any
    recipe_explanation: Any
    session_status: Any
    index: dict[str, Any]
    detail: dict[str, Any]
    lifecycle: dict[str, str]
    run_eligibility: dict[str, Any]
    status: dict[str, Any]
    setup_steps: list[dict[str, Any]]
    setup_complete: bool
    source_jobs_url: str
    compatibility_url: str
    recipe_editor_url: str
    recipe_capabilities: list[dict[str, str]]
    source_test_insight: dict[str, Any]
    safe_test_action: dict[str, str] | None

    @property
    def card(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "execution": self.execution_entry,
            "lifecycle": self.lifecycle,
            "index": self.index,
            "detail": self.detail,
            "session": self.session_status,
            "status": self.status,
            "run_eligibility": self.run_eligibility,
        }

    def template_context(self) -> dict[str, Any]:
        return {
            "title": f"Source - {self.source.name}",
            "source": self.source,
            "execution_entry": self.execution_entry,
            "recipe_artifacts": self.artifacts,
            "recipe_candidates": self.recipe_candidates,
            "recipe_generation_status": self.generation_status,
            "recipe_explanation": self.recipe_explanation,
            "recipe_capabilities": self.recipe_capabilities,
            "source_status": self.status,
            "source_setup_steps": self.setup_steps,
            "source_setup_complete": self.setup_complete,
            "source_card": self.card,
            "source_run_eligibility": self.run_eligibility,
            "source_session": self.session_status,
            "source_jobs_url": self.source_jobs_url,
            "source_safe_test_action": self.safe_test_action,
            "go_live_readiness": self.readiness,
            "compatibility_url": self.compatibility_url,
            "recipe_editor_url": self.recipe_editor_url,
        }


@dataclass
class SourceTestExecution:
    source: Any
    execution_entry: dict[str, Any] | None
    force_disabled: bool
    result: Any
    readiness: Any
    listing_index: Any
    session_status: Any
    payload: dict[str, Any]


class SourceWorkflowHandler:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.registry = SourceRegistryService(self.root)
        self.execution = ExecutionSourceService(self.root)
        self.artifacts = RecipeArtifactService(self.root)
        self.candidates = RecipeCandidateStore(self.root)
        self.generation = RecipeGenerationStatusService(self.root)
        self.readiness = SourceExecutionReadinessService(self.root)
        self.sessions = SourceSessionService(self.root)
        self.index_store = SourceListingIndexStore(self.root)
        self.jobs = JobStore(self.root, create=False)
        self.source_tests = SourceTestService(self.root)
        self.suggestions = SourceSuggestionService(self.root)
        self.disqualifications = SourceDisqualificationService(self.root)

    def overview_context(self, *, message: str = "", warning: str = "") -> dict[str, Any]:
        all_sources = self.registry.list_saved_sources(include_stats=True)
        sources = [source for source in all_sources if source.status != "archived"]
        archived_sources = [source for source in all_sources if source.status == "archived"]
        execution_by_source = self.saved_execution_by_source(all_sources)
        readiness_by_source = self.current_readiness_by_source(sources + archived_sources)
        index_by_source = self.index_store.summaries_by_source()
        seen_records = self.jobs.list_seen_records()
        auto_setup_configured = self._auto_setup_configured()
        source_cards = [
            self.overview_card_for_source(
                source,
                execution_by_source.get(source.id),
                readiness_by_source.get(source.id, SourceExecutionReadiness(source_id=source.id)),
                index_by_source.get(source.id, SourceListingIndexSummary(source_id=source.id, source_name=source.name)),
                seen_records,
                auto_setup_configured=auto_setup_configured,
            )
            for source in sources
        ]
        archived_source_cards = [
            self.overview_card_for_source(
                source,
                execution_by_source.get(source.id),
                readiness_by_source.get(source.id, SourceExecutionReadiness(source_id=source.id)),
                index_by_source.get(source.id, SourceListingIndexSummary(source_id=source.id, source_name=source.name)),
                seen_records,
                auto_setup_configured=auto_setup_configured,
            )
            for source in archived_sources
        ]
        auto_setup_eligible_count = sum(1 for card in source_cards if card["auto_setup"].get("can_start"))
        auto_setup_stale_refresh_count = sum(
            1
            for card in source_cards
            if card["auto_setup"].get("can_start") and card["auto_setup"].get("stale_recipe_source_test")
        )
        auto_setup_learning_count = sum(
            1
            for card in source_cards
            if card["auto_setup"].get("can_start") and card["auto_setup"].get("requires_llm")
        )
        stale_recipe_source_count = sum(
            1 for card in source_cards if card["run_eligibility"].get("stale_recipe_source_test")
        )
        return {
            "title": "Sources",
            "sources": sources,
            "archived_sources": archived_sources,
            "source_cards": source_cards,
            "archived_source_cards": archived_source_cards,
            "message": message,
            "warning": warning,
            "execution_by_source": execution_by_source,
            "daily_run_enabled_count": sum(
                1 for source in execution_by_source.values() if bool(source.get("enabled", True))
            ),
            "daily_run_eligible_count": sum(1 for card in source_cards if card["run_eligibility"]["eligible"]),
            "daily_run_skipped_count": sum(
                1
                for card in source_cards
                if card["run_eligibility"]["enabled"] and not card["run_eligibility"]["eligible"]
            ),
            "stale_recipe_source_count": stale_recipe_source_count,
            "implemented_source_count": sum(1 for card in source_cards if card["lifecycle"]["state"] == "implemented"),
            "indexed_source_count": sum(1 for card in source_cards if card["index"]["complete"]),
            "detail_complete_source_count": sum(1 for card in source_cards if card["detail"]["complete"]),
            "auto_setup_all": {
                "show": any(card["auto_setup"].get("show") for card in source_cards),
                "configured": auto_setup_configured,
                "can_start": auto_setup_eligible_count > 0,
                "eligible_count": auto_setup_eligible_count,
                "stale_refresh_count": auto_setup_stale_refresh_count,
                "learning_count": auto_setup_learning_count,
                "worker_limit": _auto_setup_worker_limit(self.root),
                "disabled_reason": (
                    "Add an Anthropic API key in Setup before learning new source reading plans."
                    if not auto_setup_configured
                    else "No setup-ready source URLs need automatic setup."
                ),
            },
            "disqualified_domains": self.suggestions.list_disqualified_domains(),
        }

    def suggestion_context(
        self,
        *,
        focus: str = "",
        raw_response: str = "",
        suggestions: list[Any] | None = None,
        warning: str = "",
        message: str = "",
        model: str = "",
    ) -> dict[str, Any]:
        return {
            "title": "Suggest Sources",
            "focus": focus,
            "prompt": self.suggestions.build_prompt(focus),
            "raw_response": raw_response,
            "source_suggestions": self.suggestions.annotate_existing(suggestions or []),
            "disqualified_domains": self.suggestions.list_disqualified_domains(),
            "llm_configured": self.suggestions.is_llm_configured(),
            "warning": warning,
            "message": message,
            "model": model,
        }

    def suggest_sources_with_llm(self, *, focus: str = "", llm_model: str = ""):
        return self.suggestions.suggest_with_llm(focus, llm_model=llm_model)

    def prepare_external_source_suggestions(self, *, focus: str = ""):
        return self.suggestions.prepare_external(focus)

    def apply_external_source_suggestions(self, interaction_id: str, response_text: str):
        return self.suggestions.apply_external_response(interaction_id, response_text)

    def load_external_source_suggestion_result(self, interaction_id: str):
        return self.suggestions.load_external_result(interaction_id)

    def parse_source_suggestions(self, raw_response: str) -> list[Any]:
        return self.suggestions.parse_response(raw_response)

    def existing_source_by_url(self, url: str):
        return self.registry.find_source_by_url(url)

    def existing_source_by_domain(self, url: str):
        return self.registry.find_source_by_domain(url)

    def disqualify_domain(self, domain_or_url: str, *, reason: str = ""):
        return self.suggestions.disqualify_domain(domain_or_url, reason=reason)

    def source_disqualification(self, url: str):
        return self.disqualifications.disqualification_for_url(url)

    def saved_execution_by_source(self, _sources: list[Any]) -> dict[str, dict[str, Any]]:
        config_sources = self.execution.load_config().get("sources", [])
        return {
            str(source.get("source_id") or ""): source
            for source in config_sources
            if isinstance(source, dict) and str(source.get("source_id") or "").strip()
        }

    def current_readiness_by_source(self, sources: list[Any]) -> dict[str, SourceExecutionReadiness]:
        saved_readiness = self.readiness.load_all()
        current: dict[str, SourceExecutionReadiness] = {}
        for source in sources:
            source_id = str(getattr(source, "id", "") or "").strip()
            if not source_id:
                continue
            saved = saved_readiness.get(source_id, SourceExecutionReadiness(source_id=source_id))
            current[source_id] = self.readiness.with_current_recipe_file_checks(source, saved)
        return current

    def overview_card_for_source(
        self,
        source,
        execution_entry,
        readiness,
        index_summary,
        seen_records,
        *,
        auto_setup_configured: bool | None = None,
    ) -> dict[str, Any]:
        index = self.index_status(index_summary)
        detail = self.detail_status_from_seen_records(source, index_summary, seen_records)
        status = build_source_page_status(
            source,
            execution_entry,
            readiness,
            index_status=index,
        )
        run_eligibility = build_source_run_eligibility(
            source,
            execution_entry,
            readiness,
            index_status=index,
        )
        implemented = bool(run_eligibility["eligible"])
        lifecycle = {
            "state": "implemented" if implemented else "setup",
            "label": "Implemented" if implemented else "In setup",
            "badge_class": "high" if implemented else "medium",
        }
        return {
            "source": source,
            "execution": execution_entry,
            "lifecycle": lifecycle,
            "index": index,
            "detail": detail,
            "session": None,
            "status": status,
            "run_eligibility": run_eligibility,
            "auto_setup": self._auto_setup_card(
                source,
                lifecycle,
                readiness=readiness,
                index_status=index,
                auto_setup_configured=auto_setup_configured,
            ),
        }

    def _auto_setup_configured(self) -> bool:
        try:
            return LlmService(self.root).is_configured()
        except (RuntimeError, ValueError):
            return False

    def _auto_setup_card(
        self,
        source,
        lifecycle: dict[str, str],
        *,
        readiness=None,
        index_status: dict[str, Any] | None = None,
        auto_setup_configured: bool | None = None,
    ) -> dict[str, Any]:
        return self.auto_setup_state(
            source,
            lifecycle,
            readiness=readiness,
            index_status=index_status,
            auto_setup_configured=auto_setup_configured,
        )

    def auto_setup_state(
        self,
        source,
        lifecycle: dict[str, str],
        *,
        readiness=None,
        index_status: dict[str, Any] | None = None,
        auto_setup_configured: bool | None = None,
    ) -> dict[str, Any]:
        show = (
            source.status != "archived"
            and source.kind not in {"manual", "local_yaml"}
            and lifecycle.get("state") != "implemented"
        )
        configured = self._auto_setup_configured() if auto_setup_configured is None else auto_setup_configured
        has_recipe = bool(str(getattr(source, "recipe_path", "") or "").strip())
        stale_recipe_source_test = readiness_has_stale_recipe_test(readiness)
        readiness_ready = bool(getattr(readiness, "readiness_status", "") == "ready")
        index_complete = bool(index_status and index_status.get("complete"))
        setup_complete = bool(has_recipe and readiness_ready and index_complete and not stale_recipe_source_test)
        requires_llm = not has_recipe
        disabled_reason = ""
        if setup_complete:
            disabled_reason = (
                "Automatic setup is complete. Include this source in daily runs, or run a manual source test "
                "only if you want to recheck it."
            )
        elif not configured and requires_llm:
            disabled_reason = "Add an Anthropic API key in Setup before learning a reading plan."
        elif not source.url:
            disabled_reason = "Save a source URL before using automatic source setup."
        else:
            disqualification = self.disqualifications.disqualification_for_url(source.url)
            if disqualification:
                disabled_reason = f"This domain is disqualified from automatic setup: {disqualification.reason}"
            assessment = assess_source_setup_url(source.url) if not has_recipe else None
            if not disabled_reason and assessment and not assessment.can_auto_setup:
                disabled_reason = assessment.message
        label = "Setup complete" if setup_complete else (
            "Refresh source test"
            if stale_recipe_source_test
            else "Run source test"
            if has_recipe
            else "Automatically set up"
        )
        return {
            "show": show,
            "configured": configured,
            "can_start": bool(show and (configured or not requires_llm) and source.url and not disabled_reason),
            "action": f"/sources/{source.id}/auto-setup/start",
            "label": label,
            "disabled_reason": disabled_reason,
            "requires_llm": requires_llm,
            "stale_recipe_source_test": stale_recipe_source_test,
            "setup_complete": setup_complete,
        }

    def require_source(self, source_id: str):
        source = self.registry.get_source(source_id)
        if not source:
            raise ValueError(f"Source not found: {source_id}")
        return source

    def require_recipe_source(self, source) -> None:
        if not source.recipe_path:
            raise ValueError("Only recipe-backed sources can be configured for recipe execution.")

    def require_not_archived(self, source) -> None:
        if source.status == "archived":
            raise ValueError("Archived sources cannot be prepared, enabled, or run.")

    def ensure_disabled_execution_entry(self, source) -> None:
        self.require_recipe_source(source)
        self.require_not_archived(source)
        if self.execution.find_by_source_id(source.id):
            return
        self.execution.create_or_update_recipe_source(source, enabled=False)

    def disable_execution_entry_if_present(self, source_id: str) -> bool:
        try:
            self.execution.disable(source_id)
        except KeyError:
            return False
        return True

    def add_source(self, *, name: str, url: str, recipe_path: str = "", notes: str = ""):
        return self.registry.add_source(name=name, url=url, recipe_path=recipe_path, notes=notes)

    def update_source(
        self,
        source_id: str,
        *,
        name: str,
        kind: str,
        url: str,
        status: str,
        recipe_path: str,
        notes: str,
    ):
        updated = self.registry.update_source(
            source_id,
            name=name,
            kind=kind,
            url=url,
            status=status,
            recipe_path=recipe_path,
            notes=notes,
        )
        if updated.status == "archived":
            self.disable_execution_entry_if_present(updated.id)
        return updated

    def archive_source(self, source_id: str):
        source = self.registry.archive_source(source_id)
        return source, self.disable_execution_entry_if_present(source.id)

    def restore_source(self, source_id: str):
        return self.registry.restore_source(source_id)

    def update_source_recipe(self, source_id: str, recipe_path: str):
        source = self.require_source(source_id)
        normalized_recipe_path = recipe_path.replace("\\", "/").strip()
        kind = "recipe" if normalized_recipe_path else ("job_board" if source.kind == "recipe" else source.kind)
        return self.registry.update_source(
            source_id,
            name=source.name,
            kind=kind,
            url=source.url,
            status=source.status,
            recipe_path=normalized_recipe_path,
            notes=source.notes,
        )

    def build(self, source_id: str) -> SourceWorkflowState | None:
        source = self.registry.get_source(source_id)
        if not source:
            return None
        execution_entry = self.execution.find_by_source_id(source.id)
        artifacts = self.artifacts.list_artifacts_for_source(source)
        generation_status = self.generation.build_for_source(source.id)
        readiness = self.readiness.evaluate(source.id)
        recipe_explanation = explain_recipe(source.recipe_path, root=self.root) if source.recipe_path else None
        session_status = self.source_session_status(source)
        index_summary = self.index_store.summary_for_source(source.id, source.name)
        index = self.index_status(index_summary)
        detail = self.detail_status(source, index_summary)
        status = build_source_page_status(
            source,
            execution_entry,
            readiness,
            generation_status=generation_status,
            session_status=session_status,
            index_status=index,
        )
        setup_steps = build_source_setup_steps(
            source,
            execution_entry,
            readiness,
            generation_status,
            index_status=index,
            detail_status=detail,
            session_status=session_status,
        )
        setup_complete = all(
            str(step.get("state") or "") == "complete" for step in setup_steps if not bool(step.get("optional"))
        )
        run_eligibility = build_source_run_eligibility(
            source,
            execution_entry,
            readiness,
            index_status=index,
        )
        implemented = bool(run_eligibility["eligible"])
        lifecycle = {
            "state": "implemented" if implemented else "setup",
            "label": "Implemented" if implemented else "In setup",
            "badge_class": "high" if implemented else "medium",
        }
        best_artifact_dir = generation_status.best_artifact.artifact_dir if generation_status.best_artifact else ""
        source_test_insight = self.source_test_insight(source, readiness=readiness)
        return SourceWorkflowState(
            source=source,
            execution_entry=execution_entry,
            artifacts=artifacts,
            recipe_candidates=self.candidates_for_source(source, artifacts),
            generation_status=generation_status,
            readiness=readiness,
            recipe_explanation=recipe_explanation,
            session_status=session_status,
            index=index,
            detail=detail,
            lifecycle=lifecycle,
            run_eligibility=run_eligibility,
            status=status,
            setup_steps=setup_steps,
            setup_complete=setup_complete,
            source_jobs_url=self.source_jobs_url(source.id),
            compatibility_url=self.compatibility_url(source),
            recipe_editor_url=self.recipe_editor_url(source, best_artifact_dir),
            recipe_capabilities=self.recipe_capabilities(recipe_explanation),
            source_test_insight=source_test_insight,
            safe_test_action=self.safe_test_action(source, status),
        )

    def card_for_source(self, source, execution_entry=None) -> dict[str, Any]:
        state = self.build(source.id)
        if state:
            return state.card
        return {"source": source, "execution": execution_entry, "lifecycle": {"state": "setup"}}

    def source_session_status(self, source):
        session_scope = ""
        if source.recipe_path:
            try:
                recipe = load_project_job_board_recipe(self.root, source.recipe_path)
                session_scope = recipe.access.session_scope
            except (OSError, ValueError):
                session_scope = ""
        return self.sessions.status_for_source(source.id, session_scope=session_scope)

    def source_session_context(self, source_id: str, *, message: str = "", warning: str = "") -> dict[str, Any]:
        source = self.require_source(source_id)
        return {
            "title": f"Source Session - {source.name}",
            "source": source,
            "source_session": self.source_session_status(source),
            "default_storage_state_path": f"sources/sessions/{source.id}.storage-state.json",
            "message": message,
            "warning": warning,
        }

    def record_source_session(self, source_id: str, *, storage_state_path: str = "", expires_at: str = ""):
        source = self.require_source(source_id)
        session_scope = self.source_session_status(source).session_scope
        state_path = storage_state_path.strip() or f"sources/sessions/{source.id}.storage-state.json"
        return self.sessions.record_storage_state(
            source.id,
            session_scope=session_scope,
            storage_state_path=state_path,
            expires_at=expires_at.strip(),
        )

    def launch_source_session_capture(
        self,
        runtime,
        source_id: str,
        *,
        storage_state_path: str = "",
        expires_at: str = "",
    ):
        source = self.require_source(source_id)
        session_status = self.source_session_status(source)
        state_path = storage_state_path.strip() or f"sources/sessions/{source.id}.storage-state.json"
        return runtime.launch_source_session_capture(
            source_id=source.id,
            source_name=source.name,
            source_url=source.url,
            session_scope=session_status.session_scope,
            storage_state_path=state_path,
            expires_at=expires_at.strip(),
        )

    def clear_source_session(self, source_id: str) -> None:
        self.sessions.clear(source_id)

    def source_test_context(self, source_id: str) -> dict[str, Any]:
        source = self.require_source(source_id)
        self.ensure_disabled_execution_entry(source)
        execution_entry = self.execution.find_by_source_id(source.id)
        return {
            "title": f"Source Test - {source.name}",
            "source": source,
            "execution_entry": execution_entry,
            "force_disabled": bool(execution_entry and not bool(execution_entry.get("enabled", True))),
        }

    def run_source_test(
        self,
        source_id: str,
        *,
        progress_callback=None,
        save_readiness: bool = True,
        update_session: bool = True,
    ) -> SourceTestExecution:
        context = self.source_test_context(source_id)
        source = context["source"]
        result = self.source_tests.run_test(
            source.id,
            force_disabled=bool(context["force_disabled"]),
            progress_callback=progress_callback,
        )
        if save_readiness:
            readiness = self.readiness.save_from_source_test(result)
        else:
            readiness = self.readiness.evaluate(source.id, source_test_result=result)
        session_status = (
            self.update_source_session_verification(source, result)
            if update_session
            else self.source_session_status(source)
        )
        listing_index = self.refresh_listing_index_from_source_test(result, readiness) if save_readiness else None
        return SourceTestExecution(
            source=source,
            execution_entry=context["execution_entry"],
            force_disabled=bool(context["force_disabled"]),
            result=result,
            readiness=readiness,
            listing_index=listing_index,
            session_status=session_status,
            payload=self.source_test_payload(source, result, readiness, listing_index=listing_index),
        )

    def verify_source_session(self, source_id: str) -> SourceTestExecution:
        return self.run_source_test(source_id)

    def refresh_listing_index_from_source_test(self, result, readiness):
        if getattr(readiness, "readiness_status", "") != "ready":
            return None
        return SourceListingIndexService(self.root).record_source_test_index(result)

    def update_source_session_verification(self, source, result):
        session_status = self.source_session_status(source)
        if not getattr(result, "source_access_requires_session", False) and not self.source_test_used_session(result):
            return session_status
        if self.source_test_verified_session(result):
            return self.sessions.mark_verified(
                source.id,
                session_scope=session_status.session_scope,
            )
        if self.source_test_used_session(result) or self.source_test_has_source_access_failure(result):
            return self.sessions.mark_error(
                source.id,
                self.source_session_verification_error(result),
                session_scope=session_status.session_scope,
            )
        if not getattr(result, "source_access_requires_session", False):
            return session_status
        return self.sessions.mark_error(
            source.id,
            self.source_session_verification_error(result),
            session_scope=session_status.session_scope,
        )

    def source_test_used_session(self, result) -> bool:
        return bool(
            getattr(result, "source_access_session_used", False)
            or getattr(result, "source_access_session_status", "") == "connected"
        )

    def source_test_has_source_access_failure(self, result) -> bool:
        for check in getattr(result, "capability_checks", []) or []:
            if str(check.get("capability") or "") == "source_access" and str(check.get("status") or "") == "fail":
                return True
        return False

    def source_test_verified_session(self, result) -> bool:
        if not self.source_test_used_session(result):
            return False
        if getattr(result, "status", "") not in {"success", "warning"}:
            return False
        return not self.source_test_has_source_access_failure(result)

    def source_session_verification_error(self, result) -> str:
        for check in getattr(result, "capability_checks", []) or []:
            if str(check.get("capability") or "") == "source_access" and str(check.get("status") or "") == "fail":
                detail = str(check.get("detail") or "").strip()
                if detail:
                    return detail
        if getattr(result, "source_access_session_label", ""):
            return f"Session status is {result.source_access_session_label}."
        if getattr(result, "warnings", None):
            return str(result.warnings[0])
        return "The source test did not verify the connected session."

    def enable_when_ready(self, source_id: str):
        source = self.require_source(source_id)
        self.require_recipe_source(source)
        self.require_not_archived(source)
        index_summary = self.index_store.summary_for_source(source.id, source.name)
        if not index_summary.is_indexed:
            raise RuntimeError("Refresh the listing index before including this source in the daily run.")
        return self.readiness.enable_when_ready(source.id)

    def create_or_update_execution_source(self, source_id: str, *, preserve_enabled: bool = False):
        source = self.require_source(source_id)
        self.require_recipe_source(source)
        self.require_not_archived(source)
        existing = self.execution.find_by_source_id(source.id)
        enabled = bool(existing and existing.get("enabled", True)) if preserve_enabled else False
        return self.execution.create_or_update_recipe_source(source, enabled=enabled)

    def disable_execution_source(self, source_id: str):
        source = self.require_source(source_id)
        return self.execution.disable(source.id)

    def readiness_for_source(self, source_id: str):
        source = self.require_source(source_id)
        return self.readiness.evaluate(source.id)

    def ensure_ready_for_listing_work(self, source_id: str, *, purpose: str):
        source = self.require_source(source_id)
        self.require_recipe_source(source)
        self.require_not_archived(source)
        self.ensure_disabled_execution_entry(source)
        readiness = self.readiness.evaluate(source.id)
        if readiness.readiness_status != "ready":
            fallback = (
                "Run and pass the safe source test before indexing listings."
                if purpose == "index"
                else "Run and pass the safe source test before reviewing details."
            )
            raise RuntimeError(" ".join(readiness.blockers[:3]) or fallback)
        return source, readiness

    def launch_listing_index(self, runtime, source_id: str):
        source, _readiness = self.ensure_ready_for_listing_work(source_id, purpose="index")
        return runtime.launch_source_listing_index(source.id, source.name)

    def launch_detail_review(self, runtime, source_id: str):
        source, _readiness = self.ensure_ready_for_listing_work(source_id, purpose="detail")
        index_summary = self.index_store.summary_for_source(source.id, source.name)
        if not index_summary.is_indexed:
            raise RuntimeError("Refresh the listing index before ingesting all jobs on this source.")
        return runtime.launch_source_detail_run(
            source.id,
            include_disabled_source=True,
            append_to_today=True,
        )

    def run_source_now(self, source_id: str):
        source = self.require_source(source_id)
        self.require_not_archived(source)
        execution_entry = self.execution.find_by_source_id(source.id)
        if not execution_entry:
            raise RuntimeError("This source is not available for daily-run execution.")
        if not bool(execution_entry.get("enabled", True)):
            raise RuntimeError("Enable this source before running.")
        return SingleSourceRunService(self.root).run(source.id)

    def source_test_payload(self, source, result, readiness, *, listing_index=None) -> dict[str, Any]:
        insight = self.source_test_insight(source, result=result, readiness=readiness)
        decision = self.source_test_decision(source, readiness, insight=insight)
        return {
            "ok": result.status not in {"not_found", "disabled", "failing"},
            "source_id": result.source_id,
            "source_name": result.source_name,
            "source_type": result.source_type,
            "source_enabled": result.source_enabled,
            "forced_disabled": result.forced_disabled,
            "status": result.status,
            "job_count": result.job_count,
            "warning_count": result.warning_count,
            "warnings": result.warnings,
            "jobs": [self.source_test_job_mapping(job) for job in result.jobs],
            "jobs_returned": len(result.jobs),
            "recipe_path": result.recipe_path,
            "recipe_source_name": result.recipe_source_name,
            "base_url": result.base_url,
            "mode_used": result.mode_used,
            "access_strategy": result.access_strategy,
            "api_request_count": result.api_request_count,
            "records_observed_count": result.records_observed_count,
            "json_records_extracted_count": result.json_records_extracted_count,
            "run_steps": result.run_steps,
            "pagination_configured": result.pagination_configured,
            "pagination_strategy": result.pagination_strategy,
            "pagination_ajax_url_template_present": result.pagination_ajax_url_template_present,
            "pagination_click_selector_configured": result.pagination_click_selector_configured,
            "pagination_link_count": result.pagination_link_count,
            "pagination_max_pages": result.pagination_max_pages,
            "pagination_fetch_count": result.pagination_fetch_count,
            "pagination_fetch_attempts": result.pagination_fetch_attempts,
            "pagination_duplicate_page_count": result.pagination_duplicate_page_count,
            "pagination_duplicate_ratio": result.pagination_duplicate_ratio,
            "pagination_unique_jobs_from_fetched_pages": result.pagination_unique_jobs_from_fetched_pages,
            "interactive_pagination_control_count": result.interactive_pagination_control_count,
            "source_access_requires_session": result.source_access_requires_session,
            "source_access_session_used": result.source_access_session_used,
            "source_access_session_scope": result.source_access_session_scope,
            "source_access_setup_hint": result.source_access_setup_hint,
            "source_access_session_status": result.source_access_session_status,
            "source_access_session_label": result.source_access_session_label,
            "source_access_login_gate_detected": result.source_access_login_gate_detected,
            "listing_observed_count": result.listing_observed_count,
            "listing_extracted_count": result.listing_extracted_count,
            "listing_missing_url_count": result.listing_missing_url_count,
            "listing_rejected_count": result.listing_rejected_count,
            "listing_duplicate_count": result.listing_duplicate_count,
            "listing_limit_skipped_count": result.listing_limit_skipped_count,
            "visible_total_job_count": result.visible_total_job_count,
            "listing_pages": result.listing_pages,
            "seen_new_count": result.seen_new_count,
            "seen_changed_count": result.seen_changed_count,
            "seen_previously_seen_count": result.seen_previously_seen_count,
            "count_explanations": result.count_explanations,
            "detail_follow_enabled": result.detail_follow_enabled,
            "detail_fetch_limit": result.detail_fetch_limit,
            "detail_fetch_count": result.detail_fetch_count,
            "detail_enriched_count": result.detail_enriched_count,
            "detail_request_delay_seconds": result.detail_request_delay_seconds,
            "detail_attempts": result.detail_attempts,
            "field_checks": result.field_checks,
            "capability_checks": result.capability_checks,
            "log_dir": result.log_dir,
            "log_manifest_path": result.log_manifest_path,
            "readiness_status": readiness.readiness_status,
            "readiness_summary": readiness.readiness_summary,
            "readiness_blockers": readiness.blockers,
            "readiness_warnings": readiness.warnings,
            "source_url": source.url,
            "source_test_insight": insight,
            "source_test_decision": decision,
            "listing_index": self.source_test_listing_index_mapping(listing_index),
        }

    def source_test_listing_index_mapping(self, listing_index) -> dict[str, Any]:
        if not listing_index:
            return {}
        return {
            "status": getattr(listing_index, "status", ""),
            "job_count": getattr(listing_index, "job_count", 0),
            "reviewed_in_detail_count": getattr(listing_index, "reviewed_in_detail_count", 0),
            "waiting_for_detail_count": getattr(listing_index, "waiting_for_detail_count", 0),
            "no_longer_posted_count": getattr(listing_index, "no_longer_posted_count", 0),
            "summary": getattr(listing_index, "summary", ""),
        }

    def source_test_job_mapping(self, job) -> dict[str, Any]:
        return {
            "title": job.title,
            "url": job.url,
            "source": job.source,
            "source_id": job.source_id,
            "location": job.location,
            "remote": job.remote,
            "rate": job.rate,
            "workload": job.workload,
            "posted_date": job.posted_date,
            "start_date": job.start_date,
            "languages": job.languages,
            "description": job.description,
            "description_preview": job.description_preview,
            "extraction_notes": job.extraction_notes,
        }

    def source_test_insight(self, source, *, result=None, readiness=None) -> dict[str, Any]:
        capability_checks = []
        warnings = []
        visible_total = 0
        job_count = 0
        pagination_strategy = ""
        pagination_duplicate_page_count = 0
        pagination_duplicate_ratio = 0.0
        pagination_fetch_count = 0
        unique_from_pages = 0
        interactive_pagination_control_count = 0
        source_access_requires_session = False
        source_access_session_used = False
        source_access_session_scope = ""
        source_access_status = ""
        source_access_login_gate_detected = False
        recipe_changed_after_source_test = False
        result_status = ""
        if result is not None:
            result_status = str(getattr(result, "status", "") or "")
            capability_checks = list(getattr(result, "capability_checks", []) or [])
            warnings = list(getattr(result, "warnings", []) or [])
            visible_total = int(getattr(result, "visible_total_job_count", 0) or 0)
            job_count = int(getattr(result, "job_count", 0) or 0)
            pagination_strategy = str(getattr(result, "pagination_strategy", "") or "")
            pagination_duplicate_page_count = int(getattr(result, "pagination_duplicate_page_count", 0) or 0)
            pagination_duplicate_ratio = float(getattr(result, "pagination_duplicate_ratio", 0.0) or 0.0)
            pagination_fetch_count = int(getattr(result, "pagination_fetch_count", 0) or 0)
            unique_from_pages = int(getattr(result, "pagination_unique_jobs_from_fetched_pages", 0) or 0)
            interactive_pagination_control_count = int(getattr(result, "interactive_pagination_control_count", 0) or 0)
            source_access_requires_session = bool(getattr(result, "source_access_requires_session", False))
            source_access_session_used = bool(getattr(result, "source_access_session_used", False))
            source_access_session_scope = str(getattr(result, "source_access_session_scope", "") or "")
            source_access_status = str(getattr(result, "source_access_session_status", "") or "")
            source_access_login_gate_detected = bool(getattr(result, "source_access_login_gate_detected", False))
        elif readiness is not None:
            capability_checks = list(getattr(readiness, "dry_run_capability_checks", []) or [])
            warnings = list(getattr(readiness, "dry_run_warnings", []) or [])
            job_count = int(getattr(readiness, "dry_run_job_count", 0) or 0)
            pagination_duplicate_page_count = int(getattr(readiness, "dry_run_pagination_duplicate_page_count", 0) or 0)
            pagination_duplicate_ratio = float(getattr(readiness, "dry_run_pagination_duplicate_ratio", 0.0) or 0.0)
            unique_from_pages = int(getattr(readiness, "dry_run_pagination_unique_jobs_from_fetched_pages", 0) or 0)
            checks = getattr(readiness, "checks", {}) or {}
            visible_total = int(checks.get("visible_total_job_count") or 0)
            pagination_strategy = str(checks.get("pagination_strategy") or "")
            interactive_pagination_control_count = int(checks.get("interactive_pagination_control_count") or 0)
            source_access_requires_session = bool(checks.get("source_session_required"))
            source_access_session_scope = str(checks.get("source_session_scope") or "")
            source_access_status = str(checks.get("source_session_status") or "")
            recipe_changed_after_source_test = bool(checks.get("recipe_changed_after_source_test"))

        failures = [check for check in capability_checks if str(check.get("status") or "") == "fail"]
        by_capability = {str(check.get("capability") or ""): check for check in capability_checks}
        if not pagination_strategy:
            strategy_detail = str(by_capability.get("pagination_strategy", {}).get("detail") or "").lower()
            for strategy in ["api_offset", "api_page", "browser_click", "ajax", "url"]:
                if f"declares {strategy}" in strategy_detail or f"{strategy} pagination" in strategy_detail:
                    pagination_strategy = strategy
                    break
        pagination_failure = next(
            (
                by_capability[key]
                for key in [
                    "pagination_strategy",
                    "pagination_navigation",
                    "pagination_duplicate_pages",
                    "listing_total_access",
                ]
                if key in by_capability and str(by_capability[key].get("status") or "") == "fail"
            ),
            None,
        )
        pagination_warning = (
            None
            if pagination_failure
            else pagination_warning_signal(
                warnings=warnings,
                by_capability=by_capability,
                visible_total=visible_total,
                job_count=job_count,
                pagination_fetch_count=pagination_fetch_count,
                pagination_duplicate_page_count=pagination_duplicate_page_count,
                pagination_unique_jobs_from_fetched_pages=unique_from_pages,
            )
        )
        source_access_failure = (
            by_capability.get("source_access")
            if str(by_capability.get("source_access", {}).get("status") or "") == "fail"
            else None
        )
        if not source_access_session_used:
            source_access_detail = str(by_capability.get("source_access", {}).get("detail") or "")
            source_access_session_used = "connected source session was used" in source_access_detail.lower()
        pagination_duplicate_failure = (
            str(by_capability.get("pagination_duplicate_pages", {}).get("status") or "") == "fail"
        )
        pagination_duplicate_postings = pagination_duplicate_failure
        source_access_failed = source_access_failure is not None
        browser_dependency_missing = _source_test_browser_dependency_missing(warnings, failures)
        pagination_working_with_unique_pages = bool(
            pagination_fetch_count
            and unique_from_pages > 0
            and not pagination_duplicate_postings
            and not pagination_failure
        )
        test_did_not_complete = result_status in {"failing", "not_found"}
        if recipe_changed_after_source_test:
            title = "Test the updated reading plan"
            summary = (
                "The selected reading plan has changed since the saved source test. Previous source-test findings are "
                "historical and should not be used as the current diagnosis."
            )
            recommendation = (
                "Run a fresh safe source test so the app verifies the updated plan before indexing or daily runs."
            )
            action = {"type": "link", "label": "Run source test", "href": f"/sources/{source.id}/test-run?start=1"}
        elif source_access_failure:
            title = "Source access needs attention"
            summary = (
                "The source test could not verify the connected session for this source."
                if source_access_login_gate_detected
                else "The reading plan says this source needs a connected session, but the source test could not verify one."
            )
            recommendation = "Connect or refresh the source session, then rerun the safe source test."
            action = {"type": "link", "label": "Connect session", "href": f"/sources/{source.id}/session"}
        elif test_did_not_complete:
            title = "Source test could not complete"
            summary = warnings[0] if warnings else "The source test stopped before it could verify source capabilities."
            recommendation = (
                "Run the safe source test again; if this repeats, review the source session and page access."
            )
            action = {"type": "link", "label": "Run source test", "href": f"/sources/{source.id}/test-run?start=1"}
        elif browser_dependency_missing:
            title = "Browser support required"
            summary = (
                "The selected reading plan needs browser-controlled pagination, but optional Playwright browser "
                "support is not available in this environment."
            )
            recommendation = (
                "Install the optional Playwright dependencies and Chromium, then rerun the safe source test. "
                "If the saved page evidence does not show real pagination controls, rebuild the reading plan instead."
            )
            action = {"type": "link", "label": "Run source test", "href": f"/sources/{source.id}/test-run?start=1"}
        elif pagination_failure:
            title = "Paginated page access failed"
            summary = (
                "The source test reached the first listing page, but later listing pages did not verify cleanly. "
                "The selected reading plan should be rebuilt with a different pagination strategy."
            )
            recommendation = (
                "Rebuild the reading plan so the generator focuses on pagination evidence instead of reusing "
                "the failing URL pagination rule."
            )
            action = {
                "type": "post",
                "label": "Rebuild reading plan",
                "action": f"/sources/{source.id}/reading-plan/rebuild-from-test",
            }
        elif pagination_warning:
            title = "Paginated page access needs review"
            summary = (
                "The source test found jobs across pagination, but it also found evidence that one or more "
                "pagination pages may be duplicate, incomplete, or using the wrong navigation rule."
            )
            recommendation = (
                "Rebuild the reading plan so the generator can use the pagination warning as evidence and try "
                "a more reliable pagination strategy."
            )
            action = {
                "type": "post",
                "label": "Rebuild reading plan",
                "action": f"/sources/{source.id}/reading-plan/rebuild-from-test",
            }
        elif failures:
            title = "Source test found a setup issue"
            summary = str(failures[0].get("detail") or "The source test found a capability that did not verify.")
            recommendation = "Review the failing capability before including this source in daily runs."
            action = {"type": "link", "label": "Back to source", "href": f"/sources/{source.id}"}
        else:
            title = "Source test passed"
            summary = "The selected reading plan verified the source capabilities checked by this test."
            recommendation = "Review the result, then include the source in the daily run when ready."
            action = {}

        ai_oversight = _source_test_ai_oversight(
            title=title,
            failures=failures,
            pagination_failure=bool(pagination_failure),
            pagination_warning=bool(pagination_warning),
            source_access_failed=source_access_failed,
            test_did_not_complete=test_did_not_complete,
        )
        clues = {
            "insight_title": title,
            "summary": summary,
            "recommendation": recommendation,
            "failed_capabilities": [
                {
                    "capability": check.get("capability", ""),
                    "status": check.get("status", ""),
                    "detail": check.get("detail", ""),
                }
                for check in failures
            ],
            "pagination_strategy_tested": pagination_strategy,
            "pagination_fetch_count": pagination_fetch_count,
            "pagination_duplicate_page_count": pagination_duplicate_page_count,
            "pagination_duplicate_ratio": pagination_duplicate_ratio,
            "pagination_unique_jobs_from_fetched_pages": unique_from_pages,
            "pagination_duplicate_postings": pagination_duplicate_postings,
            "pagination_working_with_unique_pages": pagination_working_with_unique_pages,
            "interactive_pagination_control_count": interactive_pagination_control_count,
            "visible_total_job_count": visible_total,
            "jobs_reached": job_count,
            "pagination_warning": pagination_warning,
            "source_access_requires_session": source_access_requires_session,
            "source_access_session_used": source_access_session_used,
            "source_access_session_scope": source_access_session_scope,
            "source_access_session_status": source_access_status,
            "source_access_failed": source_access_failed,
            "source_access_login_gate_detected": source_access_login_gate_detected,
            "warnings": warnings[:5],
            "ai_oversight": ai_oversight,
        }
        return {
            "title": title,
            "summary": summary,
            "recommendation": recommendation,
            "action": action,
            "generation_clues": clues,
            "ai_oversight": ai_oversight,
        }

    def source_test_decision(self, source, readiness, *, insight: dict[str, Any] | None = None) -> dict[str, Any]:
        insight = insight if isinstance(insight, dict) else self.source_test_insight(source, readiness=readiness)
        action = insight.get("action") if isinstance(insight.get("action"), dict) else {}
        action_path = str(action.get("action") or "")
        readiness_status = str(getattr(readiness, "readiness_status", "") or "")
        blockers = [str(item) for item in getattr(readiness, "blockers", []) or []]
        warnings = [str(item) for item in getattr(readiness, "dry_run_warnings", []) or []]
        failures = [
            check
            for check in getattr(readiness, "dry_run_capability_checks", []) or []
            if str(check.get("status") or "") == "fail"
        ]
        if readiness_status == "ready":
            return {
                "outcome": "ready",
                "summary": "The source test passed and setup can continue.",
                "should_retry_source_test": False,
                "should_regenerate_recipe": False,
            }
        if _transient_warning_only_source_test(readiness, blockers=blockers, failures=failures, warnings=warnings):
            return {
                "outcome": "retry_source_test",
                "summary": "The source test passed capability checks but hit transient detail fetch warnings.",
                "should_retry_source_test": True,
                "should_regenerate_recipe": False,
            }
        if action_path.endswith("/reading-plan/rebuild-from-test"):
            return {
                "outcome": "regenerate_recipe",
                "summary": str(insight.get("recommendation") or "Rebuild the reading plan from source-test evidence."),
                "should_retry_source_test": False,
                "should_regenerate_recipe": True,
            }
        return {
            "outcome": "needs_attention",
            "summary": str(insight.get("recommendation") or getattr(readiness, "readiness_summary", "") or ""),
            "should_retry_source_test": False,
            "should_regenerate_recipe": False,
        }

    def candidates_for_source(self, source, artifacts) -> list[Any]:
        artifact_dirs = {artifact.artifact_dir for artifact in artifacts}
        result = []
        for summary in self.candidates.list_candidates():
            try:
                candidate = self.candidates.load_candidate(summary.candidate_id)
            except ValueError:
                continue
            if self.candidate_matches_source(candidate, source, artifact_dirs):
                result.append(candidate)
        return sorted(result, key=lambda item: item.created_at, reverse=True)[:10]

    def source_for_candidate(self, candidate):
        for source in self.registry.list_sources():
            if self.candidate_matches_source(candidate, source, set()):
                return source
        return None

    def candidate_matches_source(self, candidate, source, artifact_dirs: set[str]) -> bool:
        if candidate.source_name.strip().lower() == source.name.strip().lower():
            return True
        if source.url and candidate.start_url and same_host_path(source.url, candidate.start_url):
            return True
        return bool(candidate.artifact_dir and candidate.artifact_dir in artifact_dirs)

    def index_status(self, index_summary) -> dict[str, Any]:
        return {
            "status_label": index_summary.status_label,
            "indexed_count": index_summary.indexed_count,
            "no_longer_posted_count": index_summary.no_longer_posted_count,
            "last_indexed_at": index_summary.last_indexed_at,
            "summary": (
                f"{index_summary.indexed_count} listing"
                f"{'' if index_summary.indexed_count == 1 else 's'} indexed."
                + (
                    f" {index_summary.no_longer_posted_count} historical posting"
                    f"{'' if index_summary.no_longer_posted_count == 1 else 's'} no longer posted."
                    if index_summary.no_longer_posted_count
                    else ""
                )
                if index_summary.is_indexed
                else "No listing index has been captured yet."
            ),
            "complete": index_summary.is_indexed,
        }

    def detail_status(self, source, index_summary) -> dict[str, Any]:
        return self.detail_status_from_seen_records(source, index_summary, self.jobs.list_seen_records())

    def detail_status_from_seen_records(self, source, index_summary, seen_records) -> dict[str, Any]:
        indexed_keys = {
            listing.listing_key
            for listing in index_summary.listings
            if listing.listing_key and listing.posting_status != "no_longer_posted"
        }
        active_seen_keys = {
            record.listing_key
            for record in seen_records
            if record.listing_key and record.posting_status != "no_longer_posted"
        }
        inactive_seen_keys = {
            record.listing_key
            for record in seen_records
            if record.listing_key
            and record.posting_status == "no_longer_posted"
            and self.seen_record_matches_source(record, source)
        }
        inactive_index_keys = {
            listing.listing_key
            for listing in index_summary.listings
            if listing.listing_key and listing.posting_status == "no_longer_posted"
        }
        reviewed_from_index = len(indexed_keys & active_seen_keys)
        reviewed_from_source = sum(
            1
            for record in seen_records
            if record.posting_status != "no_longer_posted" and self.seen_record_matches_source(record, source)
        )
        indexed_count = index_summary.indexed_count
        reviewed_count = min(indexed_count, reviewed_from_index) if indexed_count else reviewed_from_source
        total_count = indexed_count or reviewed_count
        waiting_count = max(0, indexed_count - reviewed_count) if indexed_count else 0
        complete = indexed_count > 0 and waiting_count == 0
        no_longer_posted_count = len(inactive_seen_keys | inactive_index_keys)
        if indexed_count:
            summary = f"{reviewed_count}/{indexed_count} current indexed postings reviewed in detail."
        elif reviewed_count:
            summary = f"{reviewed_count} postings have detail review history."
        else:
            summary = "No postings have been reviewed in detail yet."
        if no_longer_posted_count:
            summary += (
                f" {no_longer_posted_count} historical posting"
                f"{'' if no_longer_posted_count == 1 else 's'} no longer posted."
            )
        return {
            "reviewed_count": reviewed_count,
            "total_count": total_count,
            "waiting_count": waiting_count,
            "no_longer_posted_count": no_longer_posted_count,
            "complete": complete,
            "status_label": "Detail review complete" if complete else "Needs detail review",
            "summary": summary,
        }

    def safe_test_action(self, source, status: dict[str, Any]) -> dict[str, str] | None:
        primary_action = status.get("primary_action") if isinstance(status, dict) else None
        if (
            isinstance(primary_action, dict)
            and primary_action.get("type") == "link"
            and (
                "/session" in str(primary_action.get("href") or "")
                or "/test-run" in str(primary_action.get("href") or "")
            )
        ):
            return {
                "type": "link",
                "label": str(primary_action.get("label") or "Continue"),
                "href": str(primary_action.get("href") or ""),
            }
        if source.recipe_path and source.status != "archived":
            return {"type": "link", "label": "Test source safely", "href": f"/sources/{source.id}/test-run?start=1"}
        return None

    def seen_record_matches_source(self, record, source) -> bool:
        record_source = str(record.source or "").strip().lower()
        if record_source and record_source in {source.name.strip().lower(), source.id.strip().lower()}:
            return True
        if source.id == "manual-intake" and record_source.startswith("manual"):
            return True
        return bool(source.url and record.url and same_host_path(source.url, record.url))

    def recipe_capabilities(self, recipe_explanation) -> list[dict[str, str]]:
        if not recipe_explanation:
            return []
        card_detail = explanation_detail(recipe_explanation.listing_fields, "Job card") or "configured listing blocks"
        field_labels = [
            item.label
            for item in recipe_explanation.listing_fields
            if item.label not in {"Job card"} and item.detail != "Not configured."
        ]
        detail_status = "Will open detail pages" if recipe_explanation.detail_follow else "Listing page only"
        detail_text = (
            f"Checks sample one posting detail page; full runs follow retained listings with "
            f"{recipe_explanation.detail_delay:g}s delay."
            if recipe_explanation.detail_follow
            else "Does not open posting detail pages."
        )
        pagination_status = "Can follow pagination" if recipe_explanation.pagination_configured else "No pagination"
        pagination_text = (
            f"Can follow page links; full runs may scan up to {recipe_explanation.pagination_max_pages} pages."
            if recipe_explanation.pagination_configured
            else "No pagination selectors are configured."
        )
        return [
            {
                "label": "Find listing cards",
                "status": "Configured",
                "detail": f"Uses {card_detail} to find job tiles on the source page.",
            },
            {
                "label": "Read job fields",
                "status": f"{len(field_labels)} fields",
                "detail": ", ".join(field_labels) if field_labels else "No listing fields are configured.",
            },
            {"label": "Open postings", "status": detail_status, "detail": detail_text},
            {"label": "Handle pagination", "status": pagination_status, "detail": pagination_text},
        ]

    def source_jobs_url(self, source_id: str) -> str:
        params = [
            ("source_id_include", source_id),
            ("category_include", "strong"),
            ("category_include", "exploratory"),
            ("category_include", "weak"),
            ("category_include", "excluded"),
            ("category_include", "not_scored"),
            ("posting_status_include", "active"),
            ("posting_status_include", "no_longer_posted"),
        ]
        return f"/jobs?{urlencode(params)}"

    def compatibility_url(self, source) -> str:
        params = {
            "source_mode": "configured",
            "selected_source_id": source.id,
            "url": source.url,
            "recipe_path": source.recipe_path,
            "show_saved": "1",
        }
        return f"/compatibility?{urlencode(params)}"

    def recipe_editor_url(self, source, artifact_dir: str = "") -> str:
        if not source.recipe_path:
            return "/recipe-editor"
        params = {"recipe_path": source.recipe_path}
        if artifact_dir:
            params["artifact_dir"] = artifact_dir
        return f"/recipe-editor?{urlencode(params)}"


def _auto_setup_worker_limit(root: Path) -> int:
    try:
        runtime = load_profile(root).get("runtime", {})
        configured = int(runtime.get("max_parallel_sources") or 10) if isinstance(runtime, dict) else 10
    except (TypeError, ValueError):
        configured = 10
    return max(1, min(50, configured))


def explanation_detail(items, label: str) -> str:
    for item in items:
        if item.label == label:
            return item.detail
    return ""


def pagination_warning_signal(
    *,
    warnings: list[str],
    by_capability: dict[str, dict[str, Any]],
    visible_total: int,
    job_count: int,
    pagination_fetch_count: int,
    pagination_duplicate_page_count: int,
    pagination_unique_jobs_from_fetched_pages: int,
) -> str:
    warning_texts = [str(warning or "") for warning in warnings]
    capability_texts = [
        (key, by_capability.get(key, {}), str(by_capability.get(key, {}).get("detail") or ""))
        for key in [
            "listing_total_access",
            "pagination_navigation",
            "pagination_duplicate_pages",
            "pagination_strategy",
        ]
    ]
    for text in warning_texts:
        lowered = text.lower()
        if not lowered:
            continue
        duplicate_warning = "duplicate listing" in lowered or "returned only listings already seen" in lowered
        duplicate_check_failed = str(by_capability.get("pagination_duplicate_pages", {}).get("status") or "") == "fail"
        if duplicate_warning and not duplicate_check_failed:
            continue
        if pagination_duplicate_page_count <= 0 and pagination_unique_jobs_from_fetched_pages > 0:
            continue
        if "pagination" not in lowered and "listing page" not in lowered:
            continue
        if any(
            marker in lowered
            for marker in [
                "returned only listings already seen",
                "duplicate listing",
                "duplicate listings",
                "client-side pagination",
                "later result pages",
                "later pages",
                "incomplete",
            ]
        ):
            return text
    for key, check, text in capability_texts:
        if key == "listing_total_access" and str(check.get("status") or "") != "fail":
            continue
        if key == "pagination_duplicate_pages" and (
            pagination_duplicate_page_count <= 0 or str(check.get("status") or "") != "fail"
        ):
            continue
        lowered = text.lower()
        if not lowered:
            continue
        if "pagination" not in lowered and "listing page" not in lowered:
            continue
        if any(
            marker in lowered
            for marker in [
                "returned only listings already seen",
                "duplicate listing",
                "duplicate listings",
                "client-side pagination",
                "later result pages",
                "later pages",
                "incomplete",
            ]
        ):
            return text

    listing_total = by_capability.get("listing_total_access", {})
    if (
        visible_total > 0
        and job_count > 0
        and job_count < visible_total
        and pagination_fetch_count > 0
        and str(listing_total.get("status") or "") == "fail"
        and str(listing_total.get("observed") or "").lower() in {"false", "no", "0"}
    ):
        return str(listing_total.get("detail") or "The listing total was not fully reached.")

    if pagination_duplicate_page_count > 0 and visible_total > 0 and job_count < visible_total:
        return "A fetched pagination page repeated listings before the visible total was reached."

    return ""


def _source_test_browser_dependency_missing(warnings: list[str], failures: list[dict[str, Any]]) -> bool:
    texts = [str(warning or "") for warning in warnings]
    texts.extend(str(item.get("detail") or "") for item in failures if isinstance(item, dict))
    haystack = " ".join(texts).lower()
    if not haystack:
        return False
    return "playwright" in haystack and any(
        marker in haystack
        for marker in [
            "no module named",
            "unavailable",
            "not installed",
            "requires playwright",
            "playwright install",
            "executable doesn't exist",
        ]
    )


def _transient_warning_only_source_test(
    readiness,
    *,
    blockers: list[str],
    failures: list[dict[str, Any]],
    warnings: list[str],
) -> bool:
    if str(getattr(readiness, "readiness_status", "") or "") != "warning":
        return False
    if blockers or failures:
        return False
    if int(getattr(readiness, "dry_run_job_count", 0) or 0) <= 0:
        return False
    if str(getattr(readiness, "dry_run_status", "") or "") != "warning":
        return False
    if not warnings:
        return False
    transient_markers = [
        "detail fetch failed",
        "remote end closed connection",
        "remote disconnected",
        "connection aborted",
        "read timed out",
        "timeout",
        "temporarily unavailable",
    ]
    return all(any(marker in warning.lower() for marker in transient_markers) for warning in warnings)


def _source_test_ai_oversight(
    *,
    title: str,
    failures: list[dict[str, Any]],
    pagination_failure: bool,
    pagination_warning: bool,
    source_access_failed: bool,
    test_did_not_complete: bool,
) -> dict[str, Any]:
    level = 0
    reasons: list[str] = []
    if test_did_not_complete:
        level = max(level, 1)
        reasons.append("source_test_incomplete")
    if failures:
        level = max(level, 2)
        reasons.append("failed_capabilities")
    if pagination_failure or pagination_warning:
        level = max(level, 2 if pagination_failure else 1)
        reasons.append("pagination_evidence")
    if source_access_failed:
        level = max(level, 2)
        reasons.append("source_access_evidence")
    mode = "deterministic_first"
    if level == 1:
        mode = "ai_review_available"
    elif level >= 2:
        mode = "ai_rescue_after_deterministic_failure"
    return {
        "escalation_level": level,
        "mode": mode,
        "reasons": reasons,
        "bundle_failures": level >= 2,
        "summary": (
            "No AI oversight needed while source-test capabilities pass."
            if level == 0
            else f"{title}: include this diagnosis in the next learning/refinement prompt."
        ),
    }


def same_host_path(left: str, right: str) -> bool:
    left_parsed = urlparse(left if "://" in left else f"https://{left}")
    right_parsed = urlparse(right if "://" in right else f"https://{right}")
    left_host = left_parsed.netloc.lower().removeprefix("www.")
    right_host = right_parsed.netloc.lower().removeprefix("www.")
    if not left_host or left_host != right_host:
        return False
    left_path = left_parsed.path.rstrip("/")
    right_path = right_parsed.path.rstrip("/")
    return not left_path or right_path == left_path or right_path.startswith(f"{left_path}/")
