from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from job_agent.config import ROOT
from job_agent.io.yaml_store import read_yaml, write_yaml
from job_agent.paths import sources_dir
from job_agent.services.source_registry_service import SourceRegistryEntry, SourceRegistryService

EXECUTION_SOURCES_PATH = Path("sources/recruiting-sites.yaml")


@dataclass
class ExecutionSourceResult:
    entry: dict[str, Any]
    created: bool = False
    updated: bool = False


class ExecutionSourceService:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.path = sources_dir(root) / "recruiting-sites.yaml"

    def load_config(self) -> dict[str, Any]:
        data = read_yaml(self.path, {"sources": []})
        if not isinstance(data, dict):
            return {"sources": []}
        sources = data.get("sources", [])
        if not isinstance(sources, list):
            data["sources"] = []
        return data

    def list_sources(self) -> list[dict[str, Any]]:
        return [source for source in self.load_config().get("sources", []) if isinstance(source, dict)]

    def find_by_source_id(self, source_id: str) -> dict[str, Any] | None:
        source_id = source_id.strip()
        return next((source for source in self.list_sources() if str(source.get("source_id") or "") == source_id), None)

    def create_or_update_recipe_source(
        self,
        registry_source: SourceRegistryEntry,
        *,
        enabled: bool = False,
    ) -> ExecutionSourceResult:
        if not registry_source.recipe_path:
            raise ValueError("Only recipe-backed registry sources can be added to daily-run execution.")
        SourceRegistryService(self.root).set_enabled(registry_source.id, enabled)
        config = self.load_config()
        sources = config.setdefault("sources", [])
        if not isinstance(sources, list):
            sources = []
            config["sources"] = sources

        entry = self._recipe_entry(registry_source, enabled=enabled)
        for index, existing in enumerate(sources):
            if isinstance(existing, dict) and str(existing.get("source_id") or "") == registry_source.id:
                sources[index] = {**existing, **entry, "enabled": enabled}
                self._write(config)
                return ExecutionSourceResult(entry=sources[index], updated=True)

        sources.append(entry)
        self._write(config)
        return ExecutionSourceResult(entry=entry, created=True)

    def enable(self, source_id: str) -> dict[str, Any]:
        return self._set_enabled(source_id, True)

    def disable(self, source_id: str) -> dict[str, Any]:
        return self._set_enabled(source_id, False)

    def remove_source(self, source_id: str) -> bool:
        source_id = source_id.strip()
        config = self.load_config()
        sources = config.setdefault("sources", [])
        if not isinstance(sources, list):
            sources = []
            config["sources"] = sources
        kept_sources = [
            source
            for source in sources
            if not (isinstance(source, dict) and str(source.get("source_id") or "") == source_id)
        ]
        if len(kept_sources) == len(sources):
            return False
        config["sources"] = kept_sources
        self._write(config)
        return True

    def _set_enabled(self, source_id: str, enabled: bool) -> dict[str, Any]:
        registry = SourceRegistryService(self.root)
        registry_source = registry.get_source(source_id)
        config = self.load_config()
        sources = config.setdefault("sources", [])
        if not isinstance(sources, list):
            sources = []
            config["sources"] = sources

        if registry_source:
            if not registry_source.recipe_path:
                raise KeyError(f"No execution source entry exists for source_id: {source_id}")
            registry_source = registry.set_enabled(source_id, enabled)
            entry = self._recipe_entry(registry_source, enabled=enabled)
            for index, source in enumerate(sources):
                if isinstance(source, dict) and str(source.get("source_id") or "") == source_id:
                    sources[index] = {**source, **entry, "enabled": enabled}
                    self._write(config)
                    return sources[index]
            sources.append(entry)
            self._write(config)
            return entry

        for source in sources:
            if isinstance(source, dict) and str(source.get("source_id") or "") == source_id:
                source["enabled"] = enabled
                self._write(config)
                return source
        raise KeyError(f"No execution source entry exists for source_id: {source_id}")

    def _write(self, config: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_yaml(self.path, config)

    @staticmethod
    def _recipe_entry(registry_source: SourceRegistryEntry, *, enabled: bool) -> dict[str, Any]:
        return {
            "name": registry_source.name,
            "source_id": registry_source.id,
            "type": "recipe_html",
            "url": registry_source.url,
            "recipe_path": registry_source.recipe_path,
            "enabled": enabled,
        }
