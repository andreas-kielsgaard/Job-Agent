from __future__ import annotations

import re
from dataclasses import MISSING
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from job_agent.paths import resolve_project_path
from job_agent.services.recipes.models import (
    VALID_API_METHODS,
    VALID_API_PAGINATION_STRATEGIES,
    VALID_MODES,
    VALID_PAGINATION_STRATEGIES,
    AcceptRecipe,
    AccessRecipe,
    ApiFieldMapping,
    ApiPaginationRecipe,
    ApiRequestRecipe,
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


def load_project_job_board_recipe(root: Path, path: str | Path) -> JobBoardRecipe:
    resolved_path = resolve_project_path(root, path)
    try:
        return load_job_board_recipe(resolved_path)
    except FileNotFoundError as exc:
        raise ValueError(f"Recipe file not found: {path} (resolved to {resolved_path})") from exc


def job_board_recipe_from_mapping(data: dict[str, Any], label: str = "recipe") -> JobBoardRecipe:
    listing_data = data.get("listing") or {}
    if not isinstance(listing_data, dict):
        raise ValueError(f"{label}: listing must be a mapping.")
    listing_api = _api_request_section(_mapping_section(data, "listing_api"), f"{label}: listing_api")
    detail_api = _api_request_section(_mapping_section(data, "detail_api"), f"{label}: detail_api")
    if not listing_api.url:
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
        listing_api=listing_api,
        detail_api=detail_api,
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
    _validate_api_request(recipe.listing_api, f"{label}: listing_api", listing=True)
    _validate_api_request(recipe.detail_api, f"{label}: detail_api", listing=False)
    return recipe


def _api_request_section(data: dict[str, Any], label: str) -> ApiRequestRecipe:
    if not data:
        return ApiRequestRecipe()
    fields = data.get("fields") or {}
    if not isinstance(fields, dict):
        raise ValueError(f"{label}.fields must be a mapping.")
    pagination = data.get("pagination") or {}
    if not isinstance(pagination, dict):
        raise ValueError(f"{label}.pagination must be a mapping.")
    return ApiRequestRecipe(
        method=str(data.get("method") or "GET").strip().upper(),
        url=str(data.get("url") or "").strip(),
        headers=_string_mapping(data.get("headers") or {}, f"{label}.headers"),
        params=_json_mapping(data.get("params") or {}, f"{label}.params"),
        body=_json_mapping(data.get("body") or {}, f"{label}.body"),
        results_path=str(data.get("results_path") or "").strip(),
        total_path=str(data.get("total_path") or "").strip(),
        fields=ApiFieldMapping(**_string_fields(fields, ApiFieldMapping)),
        pagination=ApiPaginationRecipe(**_api_pagination_fields(pagination, label)),
    )


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


def _string_mapping(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping.")
    return {str(key).strip(): str(item).strip() for key, item in value.items() if str(key).strip()}


def _json_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping.")
    return {str(key).strip(): item for key, item in value.items() if str(key).strip()}


def _string_fields(data: dict[str, Any], cls: type) -> dict[str, str]:
    return {key: str(data.get(key) or "").strip() for key in cls.__dataclass_fields__ if key in data}


def _api_pagination_fields(data: dict[str, Any], label: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    string_fields = {"strategy", "page_param", "offset_param", "page_size_param"}
    int_fields = {"page_start", "offset_start", "page_size", "max_pages"}
    float_fields = {"request_delay_seconds"}
    for key in ApiPaginationRecipe.__dataclass_fields__:
        if key not in data:
            continue
        if key in string_fields:
            values[key] = str(data.get(key) or "").strip()
        elif key in int_fields:
            try:
                values[key] = int(data[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{label}.pagination.{key} must be an integer.") from exc
        elif key in float_fields:
            try:
                values[key] = float(data[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{label}.pagination.{key} must be a number.") from exc
    return values


def _validate_api_request(recipe: ApiRequestRecipe, label: str, *, listing: bool) -> None:
    if not recipe.url:
        return
    if recipe.method not in VALID_API_METHODS:
        raise ValueError(f"{label}.method must be one of: {', '.join(sorted(VALID_API_METHODS))}.")
    parsed = urlparse(recipe.url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{label}.url must be a public http(s) URL.")
    sensitive_headers = {key for key in recipe.headers if _sensitive_api_key(key)}
    if sensitive_headers:
        raise ValueError(f"{label}.headers must not contain credentials: {', '.join(sorted(sensitive_headers))}.")
    sensitive_params = _sensitive_mapping_keys(recipe.params)
    if sensitive_params:
        raise ValueError(f"{label}.params must not contain credentials: {', '.join(sorted(sensitive_params))}.")
    sensitive_body = _sensitive_mapping_keys(recipe.body)
    if sensitive_body:
        raise ValueError(f"{label}.body must not contain credentials: {', '.join(sorted(sensitive_body))}.")
    if listing and not recipe.results_path:
        raise ValueError(f"{label}.results_path is required.")
    if listing and not recipe.fields.title:
        raise ValueError(f"{label}.fields.title is required.")
    if listing and not (recipe.fields.url or recipe.fields.url_template):
        raise ValueError(f"{label}.fields.url or fields.url_template is required.")
    if recipe.pagination.strategy not in VALID_API_PAGINATION_STRATEGIES:
        raise ValueError(
            f"{label}.pagination.strategy must be one of: {', '.join(sorted(VALID_API_PAGINATION_STRATEGIES))}."
        )
    if recipe.pagination.strategy == "page" and not recipe.pagination.page_param:
        raise ValueError(f"{label}.pagination.page_param is required for page pagination.")
    if recipe.pagination.strategy == "offset" and not recipe.pagination.offset_param:
        raise ValueError(f"{label}.pagination.offset_param is required for offset pagination.")
    if recipe.pagination.max_pages <= 0:
        raise ValueError(f"{label}.pagination.max_pages must be a positive integer.")
    if recipe.pagination.request_delay_seconds < 0:
        raise ValueError(f"{label}.pagination.request_delay_seconds must be zero or greater.")


def _mapping_section(data: dict[str, Any], section: str) -> dict[str, Any]:
    value = data.get(section) or {}
    if not isinstance(value, dict):
        raise ValueError(f"{section} must be a mapping.")
    return value


def _sensitive_mapping_keys(value: Any, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            path = f"{prefix}.{key_text}" if prefix else key_text
            if _sensitive_api_key(key_text):
                keys.add(path)
            keys.update(_sensitive_mapping_keys(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            keys.update(_sensitive_mapping_keys(item, f"{prefix}[{index}]" if prefix else f"[{index}]"))
    return keys


def _sensitive_api_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return lowered in {
        "authorization",
        "cookie",
        "x_api_key",
        "api_key",
        "proxy_authorization",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "session",
        "session_id",
    }


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
