from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from requests.cookies import RequestsCookieJar, create_cookie

from job_agent.services.recipes.extraction import render_template_value

_DYNAMIC_REQUEST_TYPES = {"fetch", "xhr"}
_IGNORED_DYNAMIC_REQUEST_HOST_PARTS = (
    "doubleclick.net",
    "google-analytics.com",
    "googlesyndication.com",
    "googletagmanager.com",
    "hotjar.com",
    "sentry.io",
)


def fetch_static_html(
    url: str,
    timeout_seconds: int,
    *,
    session_state_path: str | Path | None = None,
) -> tuple[str, str, list[str]]:
    try:
        response = requests_get_with_session_state(
            url,
            timeout_seconds,
            user_agent="Job-Agent recipe tester (public page; low volume)",
            session_state_path=session_state_path,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ValueError(f"Fetch failed: {exc}") from exc
    return response.text, response.url, []


def fetch_rendered_html(
    url: str,
    timeout_seconds: int,
    *,
    session_state_path: str | Path | None = None,
) -> tuple[str, str, list[str]]:
    warnings: list[str] = []
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise ValueError(
            "Rendered mode requested but Playwright is unavailable. "
            "Install requirements-playwright.txt and Chromium to use rendered_html recipes."
        ) from exc

    timeout_ms = timeout_seconds * 1000
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page_options = {}
                if session_state_path and Path(session_state_path).exists():
                    page_options["storage_state"] = str(session_state_path)
                page = browser.new_page(**page_options)
                pending_dynamic_requests: set[int] = set()

                def track_dynamic_request(request) -> None:
                    if _rendered_request_blocks_snapshot(request):
                        pending_dynamic_requests.add(id(request))

                def release_dynamic_request(request) -> None:
                    pending_dynamic_requests.discard(id(request))

                page.on("request", track_dynamic_request)
                page.on("requestfinished", release_dynamic_request)
                page.on("requestfailed", release_dynamic_request)
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                networkidle_timed_out = False
                try:
                    page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 10_000))
                except PlaywrightError:
                    networkidle_timed_out = True
                snapshot_warnings = _wait_for_rendered_snapshot_ready(page, pending_dynamic_requests, timeout_ms)
                warnings.extend(_rendered_capture_warnings(networkidle_timed_out, snapshot_warnings))
                return page.content(), page.url, warnings
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise ValueError(f"Playwright render failed: {exc}") from exc


def requests_get_with_session_state(
    url: str,
    timeout_seconds: int,
    *,
    user_agent: str,
    session_state_path: str | Path | None = None,
):
    kwargs: dict[str, Any] = {
        "timeout": timeout_seconds,
        "headers": {"User-Agent": user_agent},
    }
    cookie_jar = cookie_jar_from_storage_state(session_state_path)
    if cookie_jar:
        kwargs["cookies"] = cookie_jar
    return requests.get(url, **kwargs)


def fetch_json_api(
    *,
    method: str,
    url: str,
    timeout_seconds: int,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> tuple[Any, str, list[str]]:
    method = method.strip().upper()
    if method not in {"GET", "POST"}:
        raise ValueError(f"API method must be GET or POST, not {method}.")
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "Job-Agent recipe API tester (public page-declared API; low volume)",
    }
    request_headers.update(headers or {})
    params = render_template_value(params or {}, context)
    body = render_template_value(body or {}, context)
    try:
        response = requests.request(
            method,
            render_template_value(url, context),
            timeout=timeout_seconds,
            headers=request_headers,
            params=params or None,
            json=body if method == "POST" else None,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ValueError(f"API fetch failed: {exc}") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError(f"API response was not JSON: {exc}") from exc
    warnings = []
    content_type = str(response.headers.get("content-type") or "").lower()
    if content_type and "json" not in content_type:
        warnings.append(f"API response content-type was {content_type}.")
    return payload, response.url, warnings


def cookie_jar_from_storage_state(session_state_path: str | Path | None) -> RequestsCookieJar | None:
    if not session_state_path:
        return None
    path = Path(session_state_path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    cookies = data.get("cookies") if isinstance(data, dict) else None
    if not isinstance(cookies, list):
        return None
    jar = RequestsCookieJar()
    for item in cookies:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "")
        domain = str(item.get("domain") or "").strip()
        path_value = str(item.get("path") or "/")
        if not name or not domain:
            continue
        jar.set_cookie(
            create_cookie(
                name=name,
                value=value,
                domain=domain,
                path=path_value,
                secure=bool(item.get("secure", False)),
                expires=_cookie_expires(item.get("expires")),
                rest={"HttpOnly": bool(item.get("httpOnly", False))},
            )
        )
    return jar if len(jar) else None


def _wait_for_rendered_snapshot_ready(
    page,
    pending_dynamic_requests: set[int],
    timeout_ms: int,
    *,
    quiet_ms: int = 800,
    stable_poll_count: int = 2,
    poll_ms: int = 250,
) -> list[str]:
    warnings: list[str] = []
    max_wait_ms = _rendered_snapshot_wait_ms(timeout_ms)
    if max_wait_ms <= 0:
        return warnings
    deadline = _monotonic_ms() + max_wait_ms
    quiet_since: int | None = None
    stable_count = 0
    last_html = page.content()

    while _monotonic_ms() < deadline:
        page.wait_for_timeout(poll_ms)
        current_html = page.content()
        if current_html == last_html:
            stable_count += 1
        else:
            stable_count = 0
            last_html = current_html

        if pending_dynamic_requests:
            quiet_since = None
            continue
        quiet_since = quiet_since or _monotonic_ms()
        if _monotonic_ms() - quiet_since >= quiet_ms and stable_count >= stable_poll_count:
            return warnings

    if pending_dynamic_requests:
        warnings.append(
            f"Rendered page still had {len(pending_dynamic_requests)} dynamic request(s) pending near the snapshot timeout."
        )
    elif stable_count < stable_poll_count:
        warnings.append("Rendered page was still changing near the snapshot timeout; captured best available HTML.")
    return warnings


def _rendered_capture_warnings(networkidle_timed_out: bool, snapshot_warnings: list[str]) -> list[str]:
    if snapshot_warnings:
        return list(snapshot_warnings)
    if networkidle_timed_out:
        return []
    return []


def _rendered_snapshot_wait_ms(timeout_ms: int) -> int:
    configured = os.getenv("JOB_AGENT_RENDERED_SNAPSHOT_WAIT_SECONDS", "").strip()
    if configured:
        try:
            return max(0, int(float(configured) * 1000))
        except ValueError:
            pass
    return min(max(timeout_ms // 3, 3_000), 8_000)


def _rendered_request_blocks_snapshot(request) -> bool:
    if getattr(request, "resource_type", "") not in _DYNAMIC_REQUEST_TYPES:
        return False
    hostname = urlparse(str(getattr(request, "url", "") or "")).hostname or ""
    hostname = hostname.lower()
    return not any(part in hostname for part in _IGNORED_DYNAMIC_REQUEST_HOST_PARTS)


def _monotonic_ms() -> int:
    import time

    return int(time.monotonic() * 1000)


def _cookie_expires(value) -> int | None:
    try:
        expires = int(float(value))
    except (TypeError, ValueError):
        return None
    return expires if expires > 0 else None
