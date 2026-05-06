$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Url = "http://127.0.0.1:8765/"
$HealthUrl = "http://127.0.0.1:8765/api/health"
$IdleSeconds = 120

function Get-CurrentAppVersion {
  $patterns = @(
    "job_agent\*.py",
    "job_agent\**\*.py",
    "job_agent\web\templates\**\*.html",
    "job_agent\web\static\**\*",
    "templates\**\*.j2",
    "prompts\**\*.md",
    "requirements.txt"
  )
  $sha = [System.Security.Cryptography.SHA256]::Create()
  $files = foreach ($pattern in $patterns) {
    Get-ChildItem -Path (Join-Path $Root $pattern) -File -Recurse -ErrorAction SilentlyContinue
  }
  $unique = $files | Sort-Object FullName -Unique
  $rootPrefix = $Root.TrimEnd('\') + '\'
  foreach ($file in $unique) {
    $relative = $file.FullName.Substring($rootPrefix.Length).Replace('\', '/')
    $nameBytes = [System.Text.Encoding]::UTF8.GetBytes($relative)
    [void]$sha.TransformBlock($nameBytes, 0, $nameBytes.Length, $null, 0)
    $content = [System.IO.File]::ReadAllBytes($file.FullName)
    [void]$sha.TransformBlock($content, 0, $content.Length, $null, 0)
  }
  [void]$sha.TransformFinalBlock([byte[]]::new(0), 0, 0)
  -join ($sha.Hash[0..7] | ForEach-Object { $_.ToString("x2") })
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

function Stop-JobAgentWeb {
  Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like '*job_agent.web.app*' -and $_.ProcessId -ne $PID } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
}

function Set-ActiveRunsCancelled {
  $path = Join-Path $Root "output\runs\runs.json"
  if (-not (Test-Path $path)) { return }
  try {
    $runs = Get-Content -Raw $path | ConvertFrom-Json
    $now = [DateTimeOffset]::UtcNow.ToString("yyyy-MM-ddTHH:mm:sszzz")
    foreach ($run in $runs) {
      if ($run.status -eq "running" -or $run.status -eq "pending") {
        $run.status = "cancelled"
        $run.finished_at = $now
        $run.error_message = "Cancelled by launcher because the running web app version was outdated."
      }
    }
    $runs | ConvertTo-Json -Depth 20 | Set-Content -Encoding UTF8 $path
  } catch {
    # Best effort only. The next run can still proceed.
  }
}

function Start-JobAgentWeb {
  $Python = Join-Path $Root ".venv\Scripts\python.exe"
  if (-not (Test-Path $Python)) {
    $Python = "python"
  }

  $command = @"
`$env:JOB_AGENT_IDLE_SHUTDOWN_SECONDS='$IdleSeconds'
Set-Location '$Root'
& '$Python' -m job_agent.web.app
"@

  Start-Process powershell.exe `
    -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $command `
    -WorkingDirectory $Root `
    -WindowStyle Hidden

  for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    if (Get-JobAgentHealth) {
      return
    }
  }
  throw "Job Agent web app did not become ready at $Url"
}

function Show-OutdatedDialog {
  param([string]$RunId)

  Add-Type -AssemblyName System.Windows.Forms
  Add-Type -AssemblyName System.Drawing

  $form = New-Object System.Windows.Forms.Form
  $form.Text = "Job Agent is outdated"
  $form.Size = New-Object System.Drawing.Size(560, 230)
  $form.StartPosition = "CenterScreen"
  $form.TopMost = $true

  $label = New-Object System.Windows.Forms.Label
  $label.Location = New-Object System.Drawing.Point(18, 18)
  $label.Size = New-Object System.Drawing.Size(510, 92)
  $label.Text = "A Job Agent server is already running, but it was started from an older app version.`r`n`r`nActive run: $RunId`r`n`r`nWhat do you want to do?"
  $form.Controls.Add($label)

  $abort = New-Object System.Windows.Forms.Button
  $abort.Text = "Abort run + restart"
  $abort.Location = New-Object System.Drawing.Point(18, 125)
  $abort.Size = New-Object System.Drawing.Size(150, 34)
  $abort.Add_Click({ $form.Tag = "abort"; $form.Close() })
  $form.Controls.Add($abort)

  $outdated = New-Object System.Windows.Forms.Button
  $outdated.Text = "Launch outdated"
  $outdated.Location = New-Object System.Drawing.Point(190, 125)
  $outdated.Size = New-Object System.Drawing.Size(150, 34)
  $outdated.Add_Click({ $form.Tag = "outdated"; $form.Close() })
  $form.Controls.Add($outdated)

  $wait = New-Object System.Windows.Forms.Button
  $wait.Text = "Wait, then restart"
  $wait.Location = New-Object System.Drawing.Point(362, 125)
  $wait.Size = New-Object System.Drawing.Size(150, 34)
  $wait.Add_Click({ $form.Tag = "wait"; $form.Close() })
  $form.Controls.Add($wait)

  [void]$form.ShowDialog()
  return [string]$form.Tag
}

$currentVersion = Get-CurrentAppVersion
$health = Get-JobAgentHealth

if ($health -and $health.app_version -and $health.app_version -ne $currentVersion) {
  if ($health.active_run_id) {
    $choice = Show-OutdatedDialog -RunId $health.active_run_id
    if ($choice -eq "outdated") {
      Start-Process $Url
      exit 0
    }
    if ($choice -eq "wait") {
      while ($true) {
        Start-Sleep -Seconds 5
        $health = Get-JobAgentHealth
        if (-not $health -or -not $health.active_run_id) { break }
      }
      Stop-JobAgentWeb
      Start-JobAgentWeb
      Start-Process $Url
      exit 0
    }
    if ($choice -eq "abort") {
      Set-ActiveRunsCancelled
      Stop-JobAgentWeb
      Start-JobAgentWeb
      Start-Process $Url
      exit 0
    }
    exit 0
  } else {
    Stop-JobAgentWeb
    Start-JobAgentWeb
    Start-Process $Url
    exit 0
  }
}

if (-not $health) {
  Start-JobAgentWeb
}

Start-Process $Url
