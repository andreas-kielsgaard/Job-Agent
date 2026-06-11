from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CODE_DIR = ROOT / "app" / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))


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
