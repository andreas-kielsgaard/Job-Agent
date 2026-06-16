$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$CodeDir = Join-Path $Root "app\code"
$VenvDir = Join-Path $Root "app\environment\.venv"
$Python = Join-Path $VenvDir "Scripts\python.exe"
$Requirements = Join-Path $Root "app\environment\requirements.txt"
$PlaywrightRequirements = Join-Path $Root "app\environment\requirements-playwright.txt"
$DependencyStamp = Join-Path $VenvDir ".job-agent-dependencies.json"
$DependencyStampScript = Join-Path $Root "app\environment\scripts\dependency_stamp.py"
Set-Location $Root

if (-not (Test-Path $Python)) {
  python -m venv $VenvDir
}

$env:PYTHONPATH = $CodeDir
$PlaywrightCheck = @"
from playwright.sync_api import sync_playwright
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    browser.close()
"@
& $Python $DependencyStampScript check --stamp $DependencyStamp --requirements $Requirements --requirements $PlaywrightRequirements *> $null
$DependenciesCurrent = $LASTEXITCODE -eq 0
if ($DependenciesCurrent) {
  & $Python -c $PlaywrightCheck *> $null
  $DependenciesCurrent = $LASTEXITCODE -eq 0
}
if (-not $DependenciesCurrent) {
  & $Python -m pip install -r $Requirements
  if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed."
  }
  & $Python -m pip install -r $PlaywrightRequirements
  if ($LASTEXITCODE -ne 0) {
    throw "Rendered browser dependency installation failed."
  }
  & $Python -m playwright install chromium
  if ($LASTEXITCODE -ne 0) {
    throw "Chromium browser installation failed."
  }
  & $Python $DependencyStampScript mark --stamp $DependencyStamp --requirements $Requirements --requirements $PlaywrightRequirements
  if ($LASTEXITCODE -ne 0) {
    throw "Could not write dependency verification stamp."
  }
}
& $Python -m job_agent.bootstrap --root $Root
& $Python -m job_agent.cli run-daily --use-llm --mark-seen
