$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$CodeDir = Join-Path $Root "app\code"
$Python = Join-Path $Root "app\environment\.venv\Scripts\python.exe"
$Requirements = Join-Path $Root "app\environment\requirements.txt"
Set-Location $Root

if (-not (Test-Path $Python)) {
  python -m venv (Join-Path $Root "app\environment\.venv")
}

$env:PYTHONPATH = $CodeDir
& $Python -m pip install -r $Requirements
& $Python -m job_agent.bootstrap --root $Root
& $Python -m job_agent.cli run-daily --use-llm --mark-seen
