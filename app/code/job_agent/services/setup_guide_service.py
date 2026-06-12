from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from job_agent.config import ROOT
from job_agent.env import load_env
from job_agent.io.json_store import read_json, write_json
from job_agent.io.yaml_store import read_yaml
from job_agent.paths import output_dir, profile_dir, sources_dir
from job_agent.profile_contract import build_profile_contract
from job_agent.services.cv_reference_service import CvReferenceService

STATE_PATH = Path("profile/setup-guide.json")
GUIDE_VERSION = 1


STEP_DEFINITIONS: tuple[dict[str, str], ...] = (
    {
        "id": "claude",
        "title": "Connect Claude",
        "short_title": "Claude",
        "summary": "Add an Anthropic key so CV drafting and AI review can help.",
        "href": "/setup#ai-writing",
        "action_label": "Open AI setup",
        "target_selector": "#ai-writing",
    },
    {
        "id": "cv",
        "title": "Upload CV",
        "short_title": "CV",
        "summary": "Use a reference CV as evidence for the local profile.",
        "href": "/setup#cv-reference",
        "action_label": "Open CV upload",
        "target_selector": "#cv-reference",
    },
    {
        "id": "profile",
        "title": "Review Profile",
        "short_title": "Profile",
        "summary": "Check basics, preferences, skills, cases, and match settings.",
        "href": "/setup#profile",
        "action_label": "Review profile",
        "target_selector": "#profile",
    },
    {
        "id": "sources",
        "title": "Connect Sources",
        "short_title": "Sources",
        "summary": "Add or enable one source before running regular checks.",
        "href": "/sources",
        "action_label": "Open sources",
        "target_selector": 'a[href="/sources/new"]',
    },
    {
        "id": "run",
        "title": "Execute Run",
        "short_title": "Run",
        "summary": "Start the first daily run and review the scored jobs.",
        "href": "/",
        "action_label": "Go to dashboard",
        "target_selector": ".big-action",
    },
)


class SetupGuideService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self.path = profile_dir(self.root) / "setup-guide.json"

    def build_context(self, *, current_path: str = "") -> dict[str, Any]:
        state = self.load_state()
        dismissed_steps = set(_list_of_strings(state.get("dismissed_steps")))
        observed = self._observed_state()
        steps = []
        for definition in STEP_DEFINITIONS:
            step_id = definition["id"]
            observed_step = observed.get(step_id, {})
            complete = bool(observed_step.get("complete"))
            dismissed = step_id in dismissed_steps
            steps.append(
                {
                    **definition,
                    "complete": complete,
                    "dismissed": dismissed,
                    "state": "complete" if complete else "dismissed" if dismissed else "pending",
                    "badge": "Done" if complete else "Skipped" if dismissed else "Pending",
                    "badge_class": "high" if complete else "low" if dismissed else "waiting",
                    "detail": observed_step.get("detail", ""),
                }
            )

        active_step = next((step for step in steps if not step["complete"] and not step["dismissed"]), None)
        if active_step:
            active_step["state"] = "active"
            active_step["badge"] = "Next"
            active_step["badge_class"] = "medium"
            active_step["companion_title"] = (
                "Welcome. I'll guide you through setup." if active_step["id"] == steps[0]["id"] else "Next setup step"
            )
            active_step["companion_message"] = self._companion_message(active_step["id"])

        complete_count = sum(1 for step in steps if step["complete"])
        dismissed_count = sum(1 for step in steps if step["dismissed"] and not step["complete"])
        all_complete = complete_count == len(steps)
        guide_dismissed = bool(state.get("guide_dismissed"))
        return {
            "version": GUIDE_VERSION,
            "state_path": STATE_PATH.as_posix(),
            "guide_dismissed": guide_dismissed,
            "steps": steps,
            "active_step": active_step,
            "complete_count": complete_count,
            "dismissed_count": dismissed_count,
            "total_count": len(steps),
            "progress_label": f"{complete_count}/{len(steps)} complete",
            "all_complete": all_complete,
            "show_companion": bool(active_step and not guide_dismissed),
            "show_entry_panel": bool(not guide_dismissed and not all_complete),
            "current_path": current_path,
            "observed": observed,
        }

    def load_state(self) -> dict[str, Any]:
        data = read_json(self.path, self._default_state())
        if not isinstance(data, dict):
            return self._default_state()
        state = {**self._default_state(), **data}
        state["dismissed_steps"] = _list_of_strings(state.get("dismissed_steps"))
        return state

    def dismiss_guide(self) -> dict[str, Any]:
        state = self.load_state()
        state["guide_dismissed"] = True
        state["dismissed_at"] = _utc_now()
        state["updated_at"] = state["dismissed_at"]
        self._write_state(state)
        return state

    def dismiss_step(self, step_id: str) -> dict[str, Any]:
        if step_id not in {step["id"] for step in STEP_DEFINITIONS}:
            raise KeyError(f"Unknown guide step: {step_id}")
        state = self.load_state()
        dismissed = set(_list_of_strings(state.get("dismissed_steps")))
        dismissed.add(step_id)
        state["dismissed_steps"] = sorted(dismissed)
        state["updated_at"] = _utc_now()
        self._write_state(state)
        return state

    def reset(self) -> dict[str, Any]:
        state = self._default_state()
        state["updated_at"] = _utc_now()
        self._write_state(state)
        return state

    def _write_state(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json(self.path, state)

    def _observed_state(self) -> dict[str, dict[str, Any]]:
        env = load_env(self.root)
        cv_reference = CvReferenceService(self.root).get_cv_reference()
        profile_contract = build_profile_contract(self.root, cv_reference)
        profile_observed = self._profile_observed(profile_contract)
        source_observed = self._source_observed()
        run_observed = self._run_observed()
        return {
            "claude": {
                "complete": bool(str(env.get("ANTHROPIC_API_KEY") or "").strip()),
                "detail": "Claude key is saved." if env.get("ANTHROPIC_API_KEY") else "No Anthropic API key saved yet.",
            },
            "cv": {
                "complete": bool(cv_reference or _read_text(profile_dir(self.root) / "canonical-cv.md").strip()),
                "detail": (
                    f"Reference file: {cv_reference.get('filename')}."
                    if cv_reference
                    else "No reference CV or CV narrative found yet."
                ),
            },
            "profile": profile_observed,
            "sources": source_observed,
            "run": run_observed,
        }

    def _profile_observed(self, profile_contract: dict[str, Any]) -> dict[str, Any]:
        sections = {str(section.get("key")): section for section in profile_contract.get("sections", [])}
        preferences = read_yaml(profile_dir(self.root) / "preferences.yaml", {})
        preferences = preferences if isinstance(preferences, dict) else {}
        match_engine = preferences.get("match_engine", {}) if isinstance(preferences.get("match_engine"), dict) else {}
        has_match_terms = any(
            bool(match_engine.get(key))
            for key in ("technical_keyword_groups", "module_keyword_groups", "contract_keyword_groups")
        )
        has_preferences = _has_any_value(preferences.get("availability")) or _has_any_value(
            preferences.get("location_policy")
        )
        identity_ready = sections.get("identity", {}).get("state") == "available"
        evidence_ready = (
            sections.get("skill_matrix", {}).get("state") == "available"
            or sections.get("writing_reference", {}).get("state") == "available"
        )
        complete = bool(identity_ready and evidence_ready and (has_preferences or has_match_terms))
        if complete:
            detail = "Profile basics, evidence, and preferences are usable."
        elif not identity_ready:
            detail = "Name and email are still missing."
        elif not evidence_ready:
            detail = "Add skills or CV narrative before matching jobs."
        else:
            detail = "Review availability or match preferences."
        return {"complete": complete, "detail": detail}

    def _source_observed(self) -> dict[str, Any]:
        source_root = sources_dir(self.root)
        config = read_yaml(source_root / "recruiting-sites.yaml", {"sources": []})
        config_sources = config.get("sources", []) if isinstance(config, dict) else []
        enabled_execution = [
            source for source in config_sources if isinstance(source, dict) and bool(source.get("enabled", True))
        ]
        registry = read_yaml(source_root / "source-registry.yaml", {"sources": []})
        registry_sources = registry.get("sources", []) if isinstance(registry, dict) else []
        enabled_registry_sources = [
            source
            for source in registry_sources
            if isinstance(source, dict)
            and source.get("status") != "archived"
            and bool(source.get("enabled"))
            and bool(source.get("recipe_path") or source.get("url"))
        ]
        readiness = read_yaml(source_root / "source-execution-readiness.yaml", {"sources": {}})
        readiness_sources = readiness.get("sources", {}) if isinstance(readiness, dict) else {}
        ready_source_ids = {
            str(source_id)
            for source_id, source in readiness_sources.items()
            if isinstance(source, dict) and source.get("readiness_status") == "ready"
        }
        ready_count = sum(
            1
            for source in readiness_sources.values()
            if isinstance(source, dict) and source.get("readiness_status") == "ready"
        )
        enabled_ready_count = sum(
            1
            for source in enabled_execution
            if str(source.get("source_id") or "").strip() in ready_source_ids
            or str(source.get("type") or "").strip() in {"local_yaml", "manual"}
        )
        if enabled_ready_count:
            detail = f"{enabled_ready_count} tested daily-run source(s) ready."
        elif enabled_execution:
            detail = "Daily-run sources exist, but none have passed safe testing yet."
        elif ready_count:
            detail = f"{ready_count} source test(s) ready; include a source in the daily run."
        elif registry_sources or enabled_registry_sources:
            detail = "Sources exist, but none are tested and included in the daily run yet."
        else:
            detail = "No sources configured yet."
        return {"complete": enabled_ready_count > 0, "detail": detail}

    def _run_observed(self) -> dict[str, Any]:
        runs = read_json(output_dir(self.root) / "runs" / "runs.json", [])
        if not isinstance(runs, list):
            runs = []
        normal_runs = [run for run in runs if isinstance(run, dict) and not bool(run.get("is_test"))]
        active_or_done = [
            run for run in normal_runs if str(run.get("status") or "") in {"pending", "running", "completed"}
        ]
        if active_or_done:
            latest = active_or_done[0]
            detail = f"Latest run is {latest.get('status', 'started')}."
        elif normal_runs:
            latest = normal_runs[0]
            detail = f"Latest run is {latest.get('status', 'unknown')}; try again when ready."
        else:
            detail = "No normal daily run has been started yet."
        return {"complete": bool(active_or_done), "detail": detail}

    @staticmethod
    def _default_state() -> dict[str, Any]:
        return {
            "version": GUIDE_VERSION,
            "guide_dismissed": False,
            "dismissed_steps": [],
            "updated_at": "",
            "dismissed_at": "",
        }

    @staticmethod
    def _companion_message(step_id: str) -> str:
        messages = {
            "claude": "Start by saving your Anthropic API key. You can skip this and still use deterministic matching.",
            "cv": "Upload a CV, then either extract its text or ask Claude to draft profile sections.",
            "profile": "Review the profile facts and matching preferences before searching for roles.",
            "sources": "Connect at least one source. Source tests keep untrusted pages out of daily runs.",
            "run": "Launch the first daily run from the dashboard when the setup pieces look ready.",
        }
        return messages.get(step_id, "Open the next setup step.")


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _has_any_value(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_has_any_value(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_any_value(item) for item in value)
    return bool(str(value or "").strip())
