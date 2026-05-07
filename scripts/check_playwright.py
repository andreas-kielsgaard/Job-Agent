from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from job_agent.browser.playwright_probe import probe_url

    try:
        result = probe_url("https://example.com")
    except RuntimeError as exc:
        print(f"Playwright probe failed: {exc}")
        return 1

    if result.error:
        print(f"Playwright probe failed: {result.error}")
        return 1

    print("Playwright probe succeeded.")
    print(f"Title: {result.title}")
    print(f"Status: {result.status}")
    print(f"Visible text chars: {result.visible_text_chars}")
    print(f"Artifacts: {result.html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
