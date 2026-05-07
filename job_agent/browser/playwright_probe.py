from __future__ import annotations

import argparse
import re
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from job_agent.config import ROOT
from job_agent.io.atomic import atomic_write_text

PLAYWRIGHT_INSTALL_MESSAGE = (
    "Playwright is not installed. Install with pip install -r requirements-playwright.txt "
    "and python -m playwright install chromium."
)


@dataclass
class BrowserProbeResult:
    url: str
    final_url: str = ""
    title: str = ""
    status: int | None = None
    html_path: str = ""
    text_path: str = ""
    screenshot_path: str = ""
    link_count: int = 0
    button_count: int = 0
    form_count: int = 0
    visible_text_chars: int = 0
    error: str = ""


def probe_url(
    url: str,
    root: Path = ROOT,
    screenshot: bool = False,
    timeout_ms: int = 30_000,
) -> BrowserProbeResult:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise RuntimeError(PLAYWRIGHT_INSTALL_MESSAGE) from exc

    artifact_dir = probe_artifact_dir(root, url)
    result = BrowserProbeResult(url=url)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                with suppress(PlaywrightError):
                    page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 10_000))

                html = page.content()
                visible_text = page.locator("body").inner_text(timeout=5_000) if page.locator("body").count() else ""
                html_path = artifact_dir / "rendered.html"
                text_path = artifact_dir / "visible-text.txt"
                atomic_write_text(html_path, html, encoding="utf-8")
                atomic_write_text(text_path, visible_text, encoding="utf-8")

                screenshot_path = ""
                if screenshot:
                    screenshot_file = artifact_dir / "screenshot.png"
                    screenshot_file.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(screenshot_file), full_page=True)
                    screenshot_path = str(screenshot_file)

                result = BrowserProbeResult(
                    url=url,
                    final_url=page.url,
                    title=page.title(),
                    status=response.status if response else None,
                    html_path=str(html_path),
                    text_path=str(text_path),
                    screenshot_path=screenshot_path,
                    link_count=page.locator("a").count(),
                    button_count=page.locator("button").count(),
                    form_count=page.locator("form").count(),
                    visible_text_chars=len(visible_text),
                )
            finally:
                browser.close()
    except PlaywrightError as exc:
        result.error = _playwright_error_message(exc)
    return result


def probe_artifact_dir(root: Path, url: str, now: datetime | None = None) -> Path:
    timestamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    path = root / "output" / "browser-probes" / f"{timestamp}-{slugify_url(url)}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def slugify_url(url: str) -> str:
    value = re.sub(r"^https?://", "", url.strip().lower())
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")[:80] or "url"


def _playwright_error_message(exc: Exception) -> str:
    message = str(exc)
    if "Executable doesn't exist" in message or "playwright install" in message:
        return f"{message}\n\nInstall browser binaries with: python -m playwright install chromium"
    return message


def _summary_lines(result: BrowserProbeResult) -> list[str]:
    artifact_dir = str(Path(result.html_path).parent) if result.html_path else ""
    return [
        f"URL: {result.url}",
        f"Final URL: {result.final_url or 'n/a'}",
        f"Title: {result.title or 'n/a'}",
        f"Status: {result.status if result.status is not None else 'n/a'}",
        f"Visible text chars: {result.visible_text_chars}",
        f"Links/buttons/forms: {result.link_count}/{result.button_count}/{result.form_count}",
        f"Artifacts: {artifact_dir or 'n/a'}",
        f"Error: {result.error or 'none'}",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an optional Playwright browser probe for one URL.")
    parser.add_argument("url")
    parser.add_argument("--screenshot", action="store_true", help="Save a full-page screenshot.")
    parser.add_argument("--timeout-ms", type=int, default=30_000, help="Navigation timeout in milliseconds.")
    args = parser.parse_args(argv)

    try:
        result = probe_url(args.url, screenshot=args.screenshot, timeout_ms=args.timeout_ms)
    except RuntimeError as exc:
        print(exc)
        return 1
    for line in _summary_lines(result):
        print(line)
    return 1 if result.error else 0


def as_dict(result: BrowserProbeResult) -> dict[str, Any]:
    return asdict(result)


if __name__ == "__main__":
    raise SystemExit(main())
