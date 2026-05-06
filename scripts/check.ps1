Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    . .\.venv\Scripts\Activate.ps1
}

python -m ruff check .
python -m ruff format --check .
python -m pytest --cov=job_agent --cov-report=term-missing
