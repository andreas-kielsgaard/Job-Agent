Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
Set-Location $Root
$env:PYTHONPATH = Join-Path $Root "app\code"

$Activate = Join-Path $Root "app\environment\.venv\Scripts\Activate.ps1"
if (Test-Path $Activate) {
    . $Activate
}

python -m ruff check .
python -m ruff format --check .
python app\environment\scripts\test_handler.py --coverage
