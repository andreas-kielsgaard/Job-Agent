from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from job_agent.config import ROOT, load_profile
from job_agent.digest import write_job_package
from job_agent.generator import generate_materials
from job_agent.io.atomic import atomic_write_text
from job_agent.io.json_store import read_json, write_json
from job_agent.models import Job, MatchResult

from .package_index_service import PackageIndexService


@dataclass
class MaterialUpdate:
    cv: str = ""
    application: str = ""
    form_answers: str = ""
    match_analysis: str = ""


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

    def generate_job_materials(self, job_id: str, use_llm: bool) -> dict[str, Any]:
        package = self.packages.find_package(job_id)
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
        write_json(Path(paths["index"]), refreshed)
        return refreshed
