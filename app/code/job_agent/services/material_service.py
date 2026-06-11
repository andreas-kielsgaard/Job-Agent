from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from job_agent.config import ROOT, load_profile
from job_agent.digest import write_job_package
from job_agent.generator import build_application_llm_prompt, generate_materials, select_experience, select_skills
from job_agent.io.atomic import atomic_write_text
from job_agent.io.json_store import read_json, write_json
from job_agent.llm import ExternalAgentService, LlmRequest
from job_agent.models import Job, MatchResult
from job_agent.services.application_examples_service import ApplicationExamplesService

from .package_index_service import PackageIndexService


@dataclass
class MaterialUpdate:
    cv: str = ""
    application: str = ""
    form_answers: str = ""
    match_analysis: str = ""


@dataclass
class BatchMaterialGenerationFailure:
    job_id: str
    error: str


@dataclass
class BatchMaterialGenerationResult:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    failures: list[BatchMaterialGenerationFailure] = field(default_factory=list)


class MaterialService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.packages = PackageIndexService(root)

    def save_job_materials(self, job_id: str, materials: MaterialUpdate) -> None:
        package = self.packages.find_package(job_id)
        if not package:
            raise KeyError(f"Job package not found: {job_id}")
        updates = {
            "cv": materials.cv,
            "application": materials.application,
            "form_answers": materials.form_answers,
            "match_analysis": materials.match_analysis,
        }
        for key, content in updates.items():
            path_text = package.get("paths", {}).get(key)
            if path_text:
                atomic_write_text(Path(path_text), content, encoding="utf-8")
        self.packages.mark_package_materials_generated(package, True)

    def generate_job_materials(self, job_id: str, use_llm: bool, run_id: str = "") -> dict[str, Any]:
        package = self.packages.find_package(job_id, run_id)
        if not package:
            raise KeyError(f"Job package not found: {job_id}")
        missing = self.packages.validate_package(package)
        if missing:
            raise ValueError(f"Job package missing required files: {', '.join(missing)}")
        files = self.packages.read_package_files(package)
        job = Job.from_mapping(json.loads(files["job"]))
        match = MatchResult(**json.loads(files["match"]))
        profile = load_profile(self.root)
        generated = generate_materials(
            job,
            match,
            profile,
            use_llm=use_llm,
            root=self.root,
            run_id=package.get("run_id", ""),
            stable_id=package.get("stable_id", ""),
        )
        paths = write_job_package(
            job,
            match,
            generated,
            self.packages.infer_package_date(package),
            root=self.root,
            run_id=package.get("run_id", ""),
            stable_id=package.get("stable_id", ""),
            fuzzy_key=package.get("fuzzy_key", ""),
            state=package.get("state", ""),
            application_status=package.get("application_status", "unreviewed"),
            ai_evaluation={key: value for key, value in package.items() if key.startswith("ai_")},
        )
        refreshed = read_json(Path(paths["index"]), {})
        refreshed["materials_generated"] = True
        refreshed["material_status"] = "generated"
        write_json(Path(paths["index"]), refreshed)
        return refreshed

    def prepare_external_application_generation(self, job_id: str, run_id: str = "") -> dict[str, Any]:
        package, job, match, profile = self._package_context(job_id, run_id)
        selected_experience = select_experience(job, profile)
        top_skills = select_skills(job, match, profile)
        application_examples = ApplicationExamplesService(self.root).select_relevant(job, match, profile)
        prompt = build_application_llm_prompt(
            job,
            match,
            profile,
            selected_experience,
            top_skills,
            application_examples,
            root=self.root,
        )
        interaction = ExternalAgentService(self.root).prepare(
            LlmRequest(
                prompt=prompt,
                max_tokens=700,
                purpose="application_generation",
                run_id=package.get("run_id", ""),
                associated_job_id=package.get("stable_id", ""),
            ),
            title=f"Draft application for {job.title}",
            instructions=(
                "Paste this prompt into an external agent. Paste back only the application text you want "
                "stored in the job package."
            ),
            metadata={"job_id": job_id, "run_id": package.get("run_id", "")},
        )
        return interaction.to_payload()

    def apply_external_application_generation(
        self,
        job_id: str,
        interaction_id: str,
        response_text: str,
        run_id: str = "",
    ) -> dict[str, Any]:
        completion = ExternalAgentService(self.root).complete(interaction_id, response_text)
        package, job, match, profile = self._package_context(job_id, run_id)
        generated = generate_materials(
            job,
            match,
            profile,
            use_llm=False,
            root=self.root,
            run_id=package.get("run_id", ""),
            stable_id=package.get("stable_id", ""),
            application_override=completion.text,
        )
        paths = write_job_package(
            job,
            match,
            generated,
            self.packages.infer_package_date(package),
            root=self.root,
            run_id=package.get("run_id", ""),
            stable_id=package.get("stable_id", ""),
            fuzzy_key=package.get("fuzzy_key", ""),
            state=package.get("state", ""),
            application_status=package.get("application_status", "unreviewed"),
            ai_evaluation={key: value for key, value in package.items() if key.startswith("ai_")},
        )
        refreshed = read_json(Path(paths["index"]), {})
        refreshed["materials_generated"] = True
        refreshed["material_status"] = "generated"
        refreshed["external_agent_application_interaction_id"] = interaction_id
        write_json(Path(paths["index"]), refreshed)
        return refreshed

    def generate_many(self, job_ids: list[str], use_llm: bool) -> BatchMaterialGenerationResult:
        result = BatchMaterialGenerationResult(total=len(job_ids))
        for job_id in job_ids:
            try:
                self.generate_job_materials(job_id, use_llm)
                result.succeeded += 1
            except Exception as exc:
                result.failed += 1
                result.failures.append(BatchMaterialGenerationFailure(job_id=job_id, error=str(exc)))
        return result

    def _package_context(
        self, job_id: str, run_id: str = ""
    ) -> tuple[dict[str, Any], Job, MatchResult, dict[str, Any]]:
        package = self.packages.find_package(job_id, run_id)
        if not package:
            raise KeyError(f"Job package not found: {job_id}")
        missing = self.packages.validate_package(package)
        if missing:
            raise ValueError(f"Job package missing required files: {', '.join(missing)}")
        files = self.packages.read_package_files(package)
        job = Job.from_mapping(json.loads(files["job"]))
        match = MatchResult(**json.loads(files["match"]))
        profile = load_profile(self.root)
        return package, job, match, profile
