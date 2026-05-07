# Optional Playwright Setup

Playwright is optional. The normal Job-Agent app, daily runs, setup pages, manual posting intake, and material generation should continue to work without it.

Use this only when you want to test whether your local Windows environment can run rendered-page diagnostics.

## Windows Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-playwright.txt
python -m playwright install chromium
```

The Playwright Python package and the browser binaries are separate installs. `pip install` installs the Python API; `python -m playwright install chromium` downloads the Chromium browser that Playwright controls.

If Playwright or Chromium fails to install, stop here and do not continue to rendered-page diagnostics or recipe work yet.

## Smoke Probe

After setup:

```powershell
python scripts/check_playwright.py
```

Or probe a specific URL:

```powershell
python -m job_agent.browser.playwright_probe https://example.com --screenshot
```

Probe artifacts are written under:

```text
output/browser-probes/
```

The probe saves rendered HTML, visible body text, and optionally a screenshot. It is not connected to source adapters or daily runs.
