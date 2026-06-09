from __future__ import annotations

import re
from dataclasses import MISSING
from pathlib import Path
from typing import Any

import yaml

from job_agent.services.recipes.models import (
    VALID_MODES,
    VALID_PAGINATION_STRATEGIES,
    AcceptRecipe,
    AccessRecipe,
    DetailRecipe,
    JobBoardRecipe,
    LimitRecipe,
    ListingRecipe,
    PaginationRecipe,
    PatternsRecipe,
    RejectRecipe,
    SelectorValue,
)


def load_job_board_recipe(path: Path) -> JobBoardRecipe:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Recipe {path} must be a YAML mapping.")
    return job_board_recipe_from_mapping(data, label=str(path))


def job_board_recipe_from_mapping(data: dict[str, Any], label: str = "recipe") -> JobBoardRecipe:
    listing_data = data.get("listing") or {}
    if not isinstance(listing_data, dict):
        raise ValueError(f"{label}: listing must be a mapping.")
    missing = [
        key
        for key in ["card_selector", "title_selector", "link_selector"]
        if not _has_selector_value(listing_data.get(key, ""))
    ]
    if missing:
        raise ValueError(f"{label}: missing required listing selector(s): {', '.join(missing)}.")

    mode = str(data.get("mode") or "static_html").strip()
    if mode not in VALID_MODES:
        raise ValueError(f"{label}: mode must be one of: {', '.join(sorted(VALID_MODES))}.")

    recipe = JobBoardRecipe(
        source_name=str(data.get("source_name") or "Recipe source").strip(),
        start_url=str(data.get("start_url") or "").strip(),
        mode=mode,
        listing=ListingRecipe(**_selector_fields(listing_data, ListingRecipe)),
        access=AccessRecipe(**_selector_fields(_mapping_section(data, "access"), AccessRecipe)),
        accept=AcceptRecipe(**_list_fields(_mapping_section(data, "accept"), AcceptRecipe, "accept")),
        detail=DetailRecipe(**_selector_fields(_mapping_section(data, "detail"), DetailRecipe)),
        pagination=PaginationRecipe(**_selector_fields(_mapping_section(data, "pagination"), PaginationRecipe)),
        reject=RejectRecipe(**_list_fields(_mapping_section(data, "reject"), RejectRecipe, "reject")),
        limits=LimitRecipe(**_int_fields(_mapping_section(data, "limits"), LimitRecipe)),
        patterns=PatternsRecipe(**_regex_fields(_mapping_section(data, "patterns"), PatternsRecipe, label)),
    )
    _validate_positive_int(recipe.limits.max_cards, "limits.max_cards", label)
    _validate_positive_int(recipe.detail.max_detail_pages, "detail.max_detail_pages", label)
    _validate_positive_int(recipe.pagination.max_pages, "pagination.max_pages", label)
    _validate_positive_int(recipe.limits.min_title_length, "limits.min_title_length", label)
    if recipe.limits.min_description_length < 0:
        raise ValueError(f"{label}: limits.min_description_length must be zero or greater.")
    if recipe.pagination.strategy not in VALID_PAGINATION_STRATEGIES:
        raise ValueError(
            f"{label}: pagination.strategy must be one of: {', '.join(sorted(VALID_PAGINATION_STRATEGIES))}."
        )
    if recipe.pagination.strategy == "ajax" and not recipe.pagination.ajax_url_template:
        raise ValueError(f"{label}: pagination.ajax_url_template is required for AJAX pagination.")
    if recipe.pagination.strategy == "browser_click" and not _selectors(
        recipe.pagination.click_selector or recipe.pagination.next_selector or recipe.pagination.page_link_selector
    ):
        raise ValueError(
            f"{label}: pagination.click_selector, next_selector, or page_link_selector is required "
            "for browser-click pagination."
        )
    if recipe.detail.request_delay_seconds < 0:
        raise ValueError(f"{label}: detail.request_delay_seconds must be zero or greater.")
    if recipe.pagination.request_delay_seconds < 0:
        raise ValueError(f"{label}: pagination.request_delay_seconds must be zero or greater.")
    return recipe


def _selector_fields(data: dict[str, Any], cls: type) -> dict[str, Any]:
    fields = cls.__dataclass_fields__
    values: dict[str, Any] = {}
    for key, field_info in fields.items():
        if key in data:
            if field_info.default is not MISSING and isinstance(field_info.default, bool):
                values[key] = bool(data[key])
            elif field_info.default is not MISSING and isinstance(field_info.default, int):
                values[key] = int(data[key])
            elif field_info.default is not MISSING and isinstance(field_info.default, float):
                values[key] = float(data[key])
            else:
                values[key] = _selector_value(data[key])
    return values


def _mapping_section(data: dict[str, Any], section: str) -> dict[str, Any]:
    value = data.get(section) or {}
    if not isinstance(value, dict):
        raise ValueError(f"{section} must be a mapping.")
    return value


def _list_fields(data: dict[str, Any], cls: type, label: str) -> dict[str, Any]:
    values = {}
    for key in cls.__dataclass_fields__:
        if key in data:
            value = data.get(key) or []
            if not isinstance(value, list):
                raise ValueError(f"{label}.{key} must be a list.")
            values[key] = [str(item).strip() for item in value if str(item).strip()]
    return values


def _int_fields(data: dict[str, Any], cls: type) -> dict[str, Any]:
    values = {}
    for key in cls.__dataclass_fields__:
        if key in data:
            try:
                values[key] = int(data[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"limits.{key} must be an integer.") from exc
    return values


def _regex_fields(data: dict[str, Any], cls: type, label: str) -> dict[str, Any]:
    values = {}
    for key in cls.__dataclass_fields__:
        if key not in data:
            continue
        pattern = str(data.get(key) or "").strip()
        if pattern:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"{label}: patterns.{key} is not a valid regex: {exc}") from exc
        values[key] = pattern
    return values


def _selector_value(value: Any) -> SelectorValue:
    if isinstance(value, list):
        selectors = [str(item).strip() for item in value if str(item).strip()]
        if not selectors:
            return ""
        return selectors
    if value is None:
        return ""
    return str(value).strip()


def _selectors(value: SelectorValue) -> list[str]:
    if isinstance(value, list):
        return [selector for selector in value if selector]
    return [value] if value else []


def _first_selector(value: SelectorValue) -> str:
    selectors = _selectors(value)
    return selectors[0] if selectors else ""


def _has_selector_value(value: Any) -> bool:
    return bool(_selectors(_selector_value(value)))


def _validate_positive_int(value: int, field_name: str, label: str) -> None:
    if value <= 0:
        raise ValueError(f"{label}: {field_name} must be a positive integer.")
