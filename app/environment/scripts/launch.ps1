$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$CodeDir = Join-Path $Root "app\code"
$VenvDir = Join-Path $Root "app\environment\.venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$Requirements = Join-Path $Root "app\environment\requirements.txt"
$PlaywrightRequirements = Join-Path $Root "app\environment\requirements-playwright.txt"
$DependencyStamp = Join-Path $VenvDir ".job-agent-dependencies.json"
$DependencyStampScript = Join-Path $Root "app\environment\scripts\dependency_stamp.py"
$Url = "http://127.0.0.1:8765/"
$HealthUrl = "http://127.0.0.1:8765/api/health"

function Test-PythonCandidate {
  param([string]$Exe, [string[]]$Args)
  try {
    & $Exe @Args -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" *> $null
    return $LASTEXITCODE -eq 0
  } catch {
    return $false
  }
}

function Find-Python {
  $candidates = @()
  try {
    foreach ($path in (& where.exe python 2>$null)) {
      if ($path) {
        $candidates += @{ Exe = $path; Args = @() }
      }
    }
  } catch {
  }
  $candidates += @(
    @{ Exe = "python3"; Args = @() },
    @{ Exe = "py"; Args = @("-3.11") }
  )
  foreach ($candidate in $candidates) {
    if (Test-PythonCandidate -Exe $candidate.Exe -Args $candidate.Args) {
      return $candidate
    }
  }
  return $null
}

function Install-PythonWithConsent {
  $choice = Read-Host "Python 3.11+ was not found. Install Python 3.11 with winget now? [y/N]"
  if ($choice -notin @("y", "Y", "yes", "YES")) {
    throw "Python 3.11+ is required. Install it, then run this launcher again."
  }
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw "winget is not available. Install Python 3.11+ from https://www.python.org/downloads/ and run this again."
  }
  winget install --id Python.Python.3.11 -e
}

function Get-JobAgentHealth {
  try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $HealthUrl -TimeoutSec 2
    if ($response.StatusCode -ne 200) { return $null }
    return $response.Content | ConvertFrom-Json
  } catch {
    return $null
  }
}

function Get-CurrentAppVersion {
  $script = @"
from pathlib import Path
from job_agent.web.runtime import compute_app_version
print(compute_app_version(Path(r'''$Root''')))
"@
  return (& $VenvPython -c $script).Trim()
}

function Test-HealthMatchesCurrentCheckout {
  param($Health, [string]$CurrentVersion)
  if (-not $Health -or -not $Health.app_version -or $Health.app_version -ne $CurrentVersion) {
    return $false
  }
  if (-not $Health.root) {
    return $false
  }
  try {
    return (Resolve-Path -LiteralPath ([string]$Health.root)).Path -eq $Root
  } catch {
    return [System.IO.Path]::GetFullPath([string]$Health.root).TrimEnd("\") -eq $Root.TrimEnd("\")
  }
}

function Get-JobAgentWebProcesses {
  Get-CimInstance Win32_Process |
    Where-Object {
      $_.Name -match '^python' -and
      $_.CommandLine -like '*-m job_agent.web.app*'
    }
}

function Get-JobAgentPortOwners {
  try {
    return @(
      Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    )
  } catch {
    return @()
  }
}

function Stop-JobAgentWeb {
  Get-JobAgentWebProcesses |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
}

function Remove-ExtraJobAgentWebProcesses {
  $processes = @(Get-JobAgentWebProcesses)
  if ($processes.Count -lt 2) { return }

  $owners = @(Get-JobAgentPortOwners)
  if ($owners.Count -eq 0) { return }

  $processIds = @($processes | ForEach-Object { [int]$_.ProcessId })
  $ownerIds = @($owners | ForEach-Object { [int]$_ })
  $matchingOwners = @($ownerIds | Where-Object { $processIds -contains $_ })
  if ($matchingOwners.Count -eq 0) { return }

  $processById = @{}
  foreach ($process in $processes) {
    $processById[[int]$process.ProcessId] = $process
  }
  $keepIds = [System.Collections.Generic.HashSet[int]]::new()
  foreach ($ownerId in $matchingOwners) {
    $currentId = [int]$ownerId
    while ($processById.ContainsKey($currentId)) {
      $null = $keepIds.Add($currentId)
      $parentId = [int]$processById[$currentId].ParentProcessId
      if (-not $processById.ContainsKey($parentId)) {
        break
      }
      $currentId = $parentId
    }
  }

  $processes |
    Where-Object { -not $keepIds.Contains([int]$_.ProcessId) } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
}

function Start-JobAgentWeb {
  $env:PYTHONPATH = if ($env:PYTHONPATH) { "$CodeDir;$env:PYTHONPATH" } else { $CodeDir }
  $command = @"
`$env:PYTHONPATH='$CodeDir'
`$env:JOB_AGENT_IDLE_SHUTDOWN_SECONDS='120'
Set-Location '$Root'
& '$VenvPython' -m job_agent.web.app
"@

  Start-Process powershell.exe `
    -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $command `
    -WorkingDirectory $Root `
    -WindowStyle Hidden

  for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    if (Get-JobAgentHealth) {
      return
    }
  }
  throw "Job Agent web app did not become ready at $Url"
}

function Test-DependenciesVerified {
  if (-not (Test-Path $DependencyStamp)) {
    return $false
  }
  & $VenvPython $DependencyStampScript check --stamp $DependencyStamp --requirements $Requirements --requirements $PlaywrightRequirements *> $null
  return $LASTEXITCODE -eq 0 -and (Test-PlaywrightChromiumReady)
}

function Set-DependenciesVerified {
  & $VenvPython $DependencyStampScript mark --stamp $DependencyStamp --requirements $Requirements --requirements $PlaywrightRequirements
  if ($LASTEXITCODE -ne 0) {
    throw "Could not write dependency verification stamp."
  }
}

function Test-PlaywrightChromiumReady {
  $script = @"
from playwright.sync_api import sync_playwright
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    browser.close()
"@
  & $VenvPython -c $script *> $null
  return $LASTEXITCODE -eq 0
}

$python = Find-Python
if (-not $python) {
  Install-PythonWithConsent
  $python = Find-Python
}
if (-not $python) {
  throw "Python 3.11+ is still not available on PATH."
}

if (-not (Test-Path $VenvPython)) {
  & $python["Exe"] @($python["Args"]) -m venv $VenvDir
  if (-not (Test-Path $VenvPython)) {
    throw "Virtual environment creation failed; $VenvPython was not created."
  }
}

if (-not (Test-DependenciesVerified)) {
  & $VenvPython -m pip install --upgrade pip
  if ($LASTEXITCODE -ne 0) {
    throw "pip upgrade failed."
  }
  & $VenvPython -m pip install -r $Requirements
  if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed."
  }
  & $VenvPython -m pip install -r $PlaywrightRequirements
  if ($LASTEXITCODE -ne 0) {
    throw "Rendered browser dependency installation failed."
  }
  & $VenvPython -m playwright install chromium
  if ($LASTEXITCODE -ne 0) {
    throw "Chromium browser installation failed."
  }
  Set-DependenciesVerified
}

$env:PYTHONPATH = if ($env:PYTHONPATH) { "$CodeDir;$env:PYTHONPATH" } else { $CodeDir }
& $VenvPython -m job_agent.bootstrap --root $Root

$currentVersion = Get-CurrentAppVersion
$health = Get-JobAgentHealth
if ($health -and -not (Test-HealthMatchesCurrentCheckout -Health $health -CurrentVersion $currentVersion)) {
  if ($health.active_run_id) {
    $choice = Read-Host "A different Job Agent server is running an active run ($($health.active_run_id)). Stop it and launch this checkout? [y/N]"
    if ($choice -notin @("y", "Y", "yes", "YES")) {
      Start-Process $Url
      exit 0
    }
  }
  Stop-JobAgentWeb
  $health = $null
}

if ($health -and (Test-HealthMatchesCurrentCheckout -Health $health -CurrentVersion $currentVersion)) {
  Remove-ExtraJobAgentWebProcesses
  $health = Get-JobAgentHealth
  if (-not (Test-HealthMatchesCurrentCheckout -Health $health -CurrentVersion $currentVersion)) {
    $health = $null
  }
}

if (-not $health) {
  Start-JobAgentWeb
}

Start-Process $Url
