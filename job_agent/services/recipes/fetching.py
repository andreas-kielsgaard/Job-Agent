from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests
from requests.cookies import RequestsCookieJar, create_cookie


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
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 10_000))
                except PlaywrightError:
                    warnings.append("Rendered page did not become network-idle before the polite timeout.")
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


def _cookie_expires(value) -> int | None:
    try:
        expires = int(float(value))
    except (TypeError, ValueError):
        return None
    return expires if expires > 0 else None
