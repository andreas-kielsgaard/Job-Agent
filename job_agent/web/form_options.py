from __future__ import annotations

from pathlib import Path

import yaml

from job_agent.services.source_registry_service import SourceRegistryEntry, SourceRegistryService


def recipe_options(root: Path) -> list[dict[str, str]]:
    recipes_root = root / "sources" / "recipes"
    if not recipes_root.exists():
        return []
    options = []
    for path in sorted(recipes_root.rglob("*.yaml")):
        relative = path.relative_to(root).as_posix()
        if "/examples/" in f"/{relative}":
            continue
        options.append({"label": _recipe_label(path, relative), "value": relative})
    return options


def source_options(root: Path) -> list[SourceRegistryEntry]:
    return [
        source
        for source in SourceRegistryService(root).list_sources()
        if source.url or source.recipe_path or source.kind in {"recipe", "experimental_recipe"}
    ]


def default_recipe_for_source(source: SourceRegistryEntry | None, recipes: list[dict[str, str]]) -> str:
    if not source:
        return ""
    if source.recipe_path:
        return source.recipe_path
    source_name = _normalize_label(source.name)
    for recipe in recipes:
        label = _normalize_label(recipe["label"])
        value = _normalize_label(Path(recipe["value"]).stem)
        if source_name and (source_name in label or label in source_name or source_name in value):
            return recipe["value"]
    return ""


def _recipe_label(path: Path, fallback: str) -> str:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        data = {}
    if isinstance(data, dict):
        name = str(data.get("source_name") or "").strip()
        if name:
            return name
    return Path(fallback).stem.replace("-", " ").replace("_", " ").title()


def _normalize_label(value: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in value).split())
