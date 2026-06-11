from __future__ import annotations


def source_debug_options(sources: list) -> list[dict[str, str]]:
    return [source_debug_option(source) for source in sources]


def source_debug_option(source) -> dict[str, str]:
    return {
        "id": source.id,
        "name": source.name,
        "url": source.url,
        "recipe_path": source.recipe_path,
        "kind": source.kind,
        "status": source.status,
    }


def recipe_label(recipe_path: str, recipes: list[dict[str, str]]) -> str:
    return next((recipe["label"] for recipe in recipes if recipe["value"] == recipe_path), "")
