from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from job_agent.config import ROOT, load_profile
from job_agent.highlights import build_match_highlights
from job_agent.io.json_store import read_json, write_json
from job_agent.models import Job, MatchResult
from job_agent.package_projection import match_index_fields, match_score_fields
from job_agent.scoring import score_job
from job_agent.services.ai_search_service import AiSearchEvaluation, AiSearchService, should_ai_evaluate_job
from job_agent.services.package_index_service import PackageIndexService


@dataclass
class MatchUpdateResult:
    updated: int = 0
    skipped: int = 0
    failed: int = 0


class MatchUpdateService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.packages = PackageIndexService(root)

    def recalculate_deterministic(self, packages: list[dict[str, Any]]) -> MatchUpdateResult:
        profile = load_profile(self.root)
        result = MatchUpdateResult()
        for package in packages:
            try:
                job = self._job_from_package(package)
                if not job:
                    result.skipped += 1
                    continue
                match = score_job(job, profile)
                self._write_match(package, match)
                result.updated += 1
            except (OSError, ValueError, KeyError):
                result.failed += 1
        return result

    def apply_ai_matching(self, packages: list[dict[str, Any]], *, llm_model: str = "") -> MatchUpdateResult:
        profile = load_profile(self.root)
        service = AiSearchService(self.root)
        if not service.is_configured():
            raise ValueError("Claude is not configured. Add an Anthropic API key in AI Review & Writing first.")
        result = MatchUpdateResult()
        pending_packages = []
        for package in _unique_packages(packages):
            if _has_ai_match(package):
                result.skipped += 1
                continue
            pending_packages.append(package)

        max_workers = _max_ai_workers(profile, len(pending_packages))
        if max_workers <= 1:
            for package in pending_packages:
                _count_result(result, self._apply_ai_matching_to_package(package, profile, llm_model))
            return result

        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ai-match") as executor:
            futures = {
                executor.submit(self._apply_ai_matching_to_package, package, profile, llm_model): package
                for package in pending_packages
            }
            for future in as_completed(futures):
                _count_result(result, future.result())
        return result

    def _apply_ai_matching_to_package(
        self,
        package: dict[str, Any],
        profile: dict[str, Any],
        llm_model: str,
    ) -> str:
        service = AiSearchService(self.root)
        try:
            job = self._job_from_package(package)
            match = self._match_from_package(package)
            if not job or not match:
                return "skipped"
            highlights = build_match_highlights(job, match, profile)
            if not should_ai_evaluate_job(job, match, profile, highlights):
                return "skipped"
            evaluation = service.evaluate(
                job,
                match,
                profile,
                highlights,
                run_id=str(package.get("run_id") or ""),
                stable_id=str(package.get("stable_id") or package.get("package_id") or ""),
                llm_model=llm_model,
            )
            self._write_ai_evaluation(package, match, evaluation)
            return "updated"
        except Exception as exc:
            failed = service.failed(str(exc))
            try:
                match = self._match_from_package(package)
                if match:
                    self._write_ai_evaluation(package, match, failed)
            except (OSError, ValueError, KeyError):
                pass
            return "failed"

    def _job_from_package(self, package: dict[str, Any]) -> Job | None:
        payload = self.packages.read_package_files(package).get("job", "")
        data = read_json(Path(package.get("paths", {}).get("job", "")), None) if not payload else None
        if payload:
            import json

            parsed = json.loads(payload)
        else:
            parsed = data
        return Job.from_mapping(parsed) if isinstance(parsed, dict) else None

    def _match_from_package(self, package: dict[str, Any]) -> MatchResult | None:
        path = Path(str(package.get("paths", {}).get("match") or ""))
        data = read_json(path, None) if path.exists() else None
        if not isinstance(data, dict):
            return None
        allowed = MatchResult.__dataclass_fields__.keys()
        return MatchResult(**{key: data[key] for key in allowed if key in data})

    def _write_match(self, package: dict[str, Any], match: MatchResult) -> None:
        match_path = Path(str(package.get("paths", {}).get("match") or ""))
        index_path = _index_path(package)
        index = read_json(index_path, {})
        write_json(match_path, asdict(match))
        _apply_match_fields(index, match)
        write_json(index_path, index)

    def _write_ai_evaluation(self, package: dict[str, Any], match: MatchResult, evaluation: AiSearchEvaluation) -> None:
        index_path = _index_path(package)
        index = read_json(index_path, {})
        index.update(evaluation.to_index_fields())
        index.update(match_score_fields(match, index))
        write_json(index_path, index)


def _index_path(package: dict[str, Any]) -> Path:
    path = str(package.get("_index_path") or "")
    if path:
        return Path(path)
    index_path = str(package.get("paths", {}).get("index") or "")
    if not index_path:
        raise KeyError("Package index path is missing.")
    return Path(index_path)


def _apply_match_fields(index: dict[str, Any], match: MatchResult) -> None:
    index.update(match_index_fields(match, index))


def _has_ai_match(package: dict[str, Any]) -> bool:
    status = str(package.get("ai_evaluation_status") or "").strip().lower()
    return status in {"evaluated", "failed"} or _int_or_none(package.get("ai_match_score")) is not None


def _unique_packages(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen: set[str] = set()
    for package in packages:
        key = str(package.get("_index_path") or package.get("paths", {}).get("index") or "")
        if not key:
            key = str(package.get("stable_id") or package.get("package_id") or id(package))
        if key in seen:
            continue
        seen.add(key)
        result.append(package)
    return result


def _max_ai_workers(profile: dict[str, Any], pending_count: int) -> int:
    if pending_count <= 0:
        return 1
    runtime = profile.get("runtime", {}) if isinstance(profile.get("runtime"), dict) else {}
    try:
        configured = int(runtime.get("max_parallel_ai_matches") or 3)
    except (TypeError, ValueError):
        configured = 3
    return max(1, min(6, configured, pending_count))


def _count_result(result: MatchUpdateResult, status: str) -> None:
    if status == "updated":
        result.updated += 1
    elif status == "failed":
        result.failed += 1
    else:
        result.skipped += 1


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
