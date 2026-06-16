# Playwright Setup

The launcher installs Playwright and Chromium into `app/environment/.venv` because rendered source setup and some recipe tests need a browser. The normal app should still import without Playwright in constrained environments, but setup-complete source automation should use the launcher-managed environment.

Use this page when you want to repair or verify the rendered-browser environment manually.

## Windows Setup

```powershell
.\app\environment\.venv\Scripts\python.exe -m pip install -r app\environment\requirements-playwright.txt
.\app\environment\.venv\Scripts\python.exe -m playwright install chromium
```

The Playwright Python package and the browser binaries are separate installs. `pip install` installs the Python API; `python -m playwright install chromium` downloads the Chromium browser that Playwright controls.

If Playwright or Chromium fails to install, stop here and do not continue to rendered-page diagnostics or recipe work yet.

## Smoke Probe

After setup:

```powershell
.\app\environment\.venv\Scripts\python.exe app\environment\scripts\check_playwright.py
```

Or probe a specific URL:

```powershell
.\app\environment\.venv\Scripts\python.exe -m job_agent.browser.playwright_probe https://example.com --screenshot
```

Probe artifacts are written under:

```text
runtime/output/browser-probes/
```

The probe saves rendered HTML, visible body text, and optionally a screenshot. It is not connected to source adapters or daily runs.
