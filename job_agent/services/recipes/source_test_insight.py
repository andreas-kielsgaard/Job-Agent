from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import yaml


def apply_source_test_insight_to_recipe(recipe_data: dict, prompt_payload: dict[str, Any]) -> list[str]:
    insight = source_test_insight_from_payload(prompt_payload)
    warnings: list[str] = []
    if _source_test_session_indicated(insight):
        _ensure_recipe_session_access(recipe_data, prompt_payload)

    pagination = recipe_data.get("pagination")
    if not isinstance(pagination, dict):
        return warnings
    required_strategy = _source_test_required_pagination_strategy(insight)
    current_strategy = str(pagination.get("strategy") or "").strip().lower()
    if required_strategy == "browser_click" and current_strategy != "browser_click":
        click_selector = _best_browser_click_selector(prompt_payload, pagination)
        request_delay = pagination.get("request_delay_seconds", 1.0)
        max_pages = _max_pages_from_pagination_evidence(pagination, prompt_payload)
        if click_selector:
            pagination.clear()
            pagination.update(
                {
                    "strategy": "browser_click",
                    "click_selector": click_selector,
                    "max_pages": max_pages,
                    "request_delay_seconds": request_delay,
                }
            )
            warnings.append(
                "Source test observed interactive pagination controls and rejected the current pagination strategy; "
                "switched to browser-click pagination."
            )
            return warnings
        warnings.append(
            "Source test requires browser-click pagination, but no click selector evidence was available."
        )
        return warnings
    if not _source_test_url_pagination_failed(insight):
        return warnings
    if current_strategy != "url":
        return warnings

    ajax_template = _best_ajax_pagination_template(prompt_payload.get("observed_ajax_pagination_templates"), recipe_data)
    request_delay = pagination.get("request_delay_seconds", 1.0)
    max_pages = _max_pages_from_pagination_evidence(pagination, prompt_payload)
    if ajax_template:
        pagination.clear()
        pagination.update(
            {
                "strategy": "ajax",
                "ajax_url_template": ajax_template,
                "max_pages": max_pages,
                "request_delay_seconds": request_delay,
            }
        )
        warnings.append(
            "Source test proved URL pagination returned duplicate or incomplete pages; switched to observed AJAX pagination."
        )
        return warnings

    click_selector = _best_browser_click_selector(prompt_payload, pagination)
    if click_selector:
        pagination.clear()
        pagination.update(
            {
                "strategy": "browser_click",
                "click_selector": click_selector,
                "max_pages": max_pages,
                "request_delay_seconds": request_delay,
            }
        )
        warnings.append(
            "Source test proved URL pagination returned duplicate or incomplete pages; switched to browser-click pagination."
        )
        return warnings

    warnings.append(
        "Source test proved URL pagination returned duplicate or incomplete pages, but no alternate pagination evidence was available."
    )
    return warnings


def source_test_recipe_warnings(recipe: Any, insight: dict[str, Any]) -> list[str]:
    if _source_test_required_pagination_strategy(insight) == "browser_click" and (
        str(recipe.pagination.strategy or "").strip().lower() != "browser_click"
    ):
        return [
            "Source test observed interactive pagination controls and rejected this pagination strategy; choose browser-click pagination."
        ]
    if not _source_test_url_pagination_failed(insight):
        return []
    if str(recipe.pagination.strategy or "").strip().lower() != "url":
        return []
    return [
        "Source test already proved URL pagination returned duplicate or incomplete pages; choose AJAX or browser-click pagination."
    ]


def suggestion_conflicts_with_source_test_insight(result: Any) -> bool:
    try:
        data = yaml.safe_load(result.suggested_recipe_yaml) or {}
    except yaml.YAMLError:
        return False
    if not isinstance(data, dict):
        return False
    pagination = data.get("pagination") or {}
    if not isinstance(pagination, dict):
        return False
    strategy = str(pagination.get("strategy") or "").strip().lower()
    required_strategy = _source_test_required_pagination_strategy(result.source_test_insight)
    if required_strategy and strategy != required_strategy:
        return True
    return _source_test_url_pagination_failed(result.source_test_insight) and strategy == "url"


def source_test_insight_from_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    insight = payload.get("source_test_insight")
    return insight if isinstance(insight, dict) else {}


def _source_test_required_pagination_strategy(insight: dict[str, Any]) -> str:
    if _source_test_browser_click_required(insight):
        return "browser_click"
    return ""


def _source_test_browser_click_required(insight: dict[str, Any]) -> bool:
    if not isinstance(insight, dict) or not insight:
        return False
    strategy = str(insight.get("pagination_strategy_tested") or "").strip().lower()
    if strategy == "browser_click":
        return False
    failed = insight.get("failed_capabilities")
    if isinstance(failed, list):
        for item in failed:
            if not isinstance(item, dict) or str(item.get("status") or "") != "fail":
                continue
            capability = str(item.get("capability") or "").strip()
            detail = str(item.get("detail") or "").lower()
            if capability == "browser_click_pagination" and "browser-click pagination" in detail:
                return True
            if capability == "pagination_strategy" and "does not declare browser-click pagination" in detail:
                return True
    haystack = _source_test_text(insight)
    interactive_count = _positive_int(insight.get("interactive_pagination_control_count"), 0)
    return bool(
        interactive_count
        and "browser-click pagination" in haystack
        and (
            "does not use browser-click pagination" in haystack
            or "does not declare browser-click pagination" in haystack
            or "interactive pagination controls were observed" in haystack
        )
    )


def _source_test_url_pagination_failed(insight: dict[str, Any]) -> bool:
    if not isinstance(insight, dict) or not insight:
        return False
    strategy = str(insight.get("pagination_strategy_tested") or "").strip().lower()
    if strategy and strategy != "url":
        return False
    duplicate_ratio = _float_value(insight.get("pagination_duplicate_ratio"))
    haystack = _source_test_text(insight)
    return (
        strategy == "url"
        and (
            "paginated page access failed" in haystack
            or "url pagination" in haystack and "duplicate" in haystack
            or "duplicate listings" in haystack
            or "duplicate pages" in haystack
            or duplicate_ratio >= 0.8
        )
    )


def _source_test_session_indicated(insight: dict[str, Any]) -> bool:
    if not isinstance(insight, dict) or not insight:
        return False
    status = str(insight.get("source_access_session_status") or "").strip().lower()
    if status in {"connected", "verified", "usable"}:
        return True
    haystack = _source_test_text(insight)
    return "logged-in session" in haystack or "connected session" in haystack or "requires a session" in haystack


def _source_test_text(insight: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ["insight_title", "title", "summary", "recommendation"]:
        parts.append(str(insight.get(key) or ""))
    for warning in _list_value(insight.get("warnings")):
        parts.append(warning)
    failed = insight.get("failed_capabilities")
    if isinstance(failed, list):
        for item in failed:
            if isinstance(item, dict):
                parts.append(str(item.get("capability") or ""))
                parts.append(str(item.get("detail") or ""))
                parts.append(str(item.get("status") or ""))
            else:
                parts.append(str(item))
    return " ".join(parts).lower()


def _ensure_recipe_session_access(recipe_data: dict, prompt_payload: dict[str, Any]) -> None:
    access = recipe_data.setdefault("access", {})
    if not isinstance(access, dict):
        access = {}
        recipe_data["access"] = access
    access["requires_session"] = True
    if not str(access.get("session_scope") or "").strip():
        access["session_scope"] = _host_from_recipe_data(recipe_data, prompt_payload)
    if not str(access.get("setup_hint") or "").strip():
        access["setup_hint"] = "Connect a source session before verifying pagination beyond the public listing page."


def _host_from_recipe_data(recipe_data: dict, prompt_payload: dict[str, Any]) -> str:
    for value in [
        recipe_data.get("start_url"),
        prompt_payload.get("start_url") if isinstance(prompt_payload, dict) else "",
        prompt_payload.get("capture_url") if isinstance(prompt_payload, dict) else "",
    ]:
        parsed = urlparse(str(value or ""))
        if parsed.netloc:
            return parsed.netloc.lower()
    return ""


def _best_ajax_pagination_template(observations: Any, recipe_data: dict[str, Any]) -> str:
    candidates: list[tuple[int, str]] = []
    if not isinstance(observations, list):
        return ""
    start_url = str(recipe_data.get("start_url") or "")
    for index, item in enumerate(observations):
        template = item.get("ajax_url_template") if isinstance(item, dict) else item
        normalized = _normalize_ajax_template(str(template or ""), start_url=start_url)
        if not normalized:
            continue
        parsed = urlparse(normalized)
        query_param_count = len([part for part in parsed.query.split("&") if part])
        score = query_param_count + (10 if parsed.fragment else 0) + (len(normalized) // 500) + index
        candidates.append((score, normalized))
    return sorted(candidates)[0][1] if candidates else ""


def _normalize_ajax_template(value: str, *, start_url: str = "") -> str:
    template = value.strip().strip("'\"")
    if not template or "{page" not in template:
        return ""
    template = template.replace("\\/", "/").rstrip("\\").rstrip()
    if "\\" in template:
        return ""
    parsed = urlparse(template)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    if "//" in parsed.path:
        template = parsed._replace(path=re.sub(r"/{2,}", "/", parsed.path)).geturl()
        parsed = urlparse(template)
    path_without_slashes = parsed.path.lstrip("/")
    if path_without_slashes.startswith(parsed.netloc):
        return ""
    start = urlparse(start_url)
    if start.netloc == parsed.netloc and start.path.rstrip("/") == parsed.path.rstrip("/"):
        ajax_haystack = " ".join([parsed.path, parsed.query]).lower()
        if not any(token in ajax_haystack for token in ["ajax", "api", "json", "graphql", "load"]):
            return ""
    return template


def _best_browser_click_selector(prompt_payload: dict[str, Any], pagination: dict[str, Any]) -> str:
    controls = prompt_payload.get("observed_interactive_pagination_controls") if isinstance(prompt_payload, dict) else []
    if isinstance(controls, list) and controls:
        selector = str(pagination.get("click_selector") or "").strip()
        if selector:
            return selector
        for control in controls:
            selector = _selector_from_control_hint(str(control or ""))
            if selector:
                return selector
        for key in ["next_selector", "page_link_selector"]:
            selector = str(pagination.get(key) or "").strip()
            if selector and "href" not in selector:
                return selector
    selector = str(pagination.get("click_selector") or "").strip()
    return selector


def _selector_from_control_hint(value: str) -> str:
    classes = [part for part in re.split(r"\s+", value.strip()) if part]
    if len(classes) >= 2 and any("next" in part.lower() or "paginator" in part.lower() for part in classes):
        clean = [re.sub(r"[^A-Za-z0-9_-]", "", part) for part in classes[:3]]
        clean = [part for part in clean if part]
        if clean:
            return "." + ".".join(clean)
    if value.strip().lower() in {"next", "next page", "next-page"}:
        return '[aria-label="next-page"]'
    return ""


def _max_pages_from_pagination_evidence(pagination: dict[str, Any], prompt_payload: dict[str, Any]) -> int:
    max_pages = _positive_int(pagination.get("max_pages"), 2)
    for item in prompt_payload.get("observed_ajax_pagination_templates", []) if isinstance(prompt_payload, dict) else []:
        if isinstance(item, dict):
            max_pages = max(max_pages, _positive_int(item.get("observed_page"), 0))
    for item in prompt_payload.get("observed_pagination_links", []) if isinstance(prompt_payload, dict) else []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "")
        if label.isdigit():
            max_pages = max(max_pages, int(label))
        for match in re.finditer(r"(?:[?&]pagenr=|/page/)(\d+)", str(item.get("url") or "")):
            max_pages = max(max_pages, int(match.group(1)))
    return max(2, min(max_pages, 50))


def _list_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value:
        return [str(value).strip()]
    return []


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _float_value(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
