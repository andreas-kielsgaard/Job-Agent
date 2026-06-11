param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Resolve-CodexCli {
    if ($env:CODEX_CLI_PATH -and (Test-Path -LiteralPath $env:CODEX_CLI_PATH)) {
        return (Resolve-Path -LiteralPath $env:CODEX_CLI_PATH).Path
    }

    $binRoot = Join-Path $env:LOCALAPPDATA "OpenAI\Codex\bin"
    if (Test-Path -LiteralPath $binRoot) {
        $candidate = Get-ChildItem -Path $binRoot -Recurse -Filter codex.exe -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($candidate) {
            return $candidate.FullName
        }
    }

    $command = Get-Command codex -ErrorAction SilentlyContinue
    if ($command -and $command.Source -notlike "*\WindowsApps\*") {
        return $command.Source
    }

    throw "Could not locate a runnable codex.exe. Open Codex once or set CODEX_CLI_PATH."
}

$repoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$logDir = Join-Path $repoRoot "output\automation-logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logPath = Join-Path $logDir "git-maintenance-$timestamp.log"
$codexCli = Resolve-CodexCli

$prompt = @"
Run unattended git maintenance for this repository.

Use docs/git-maintenance.md as the operating instructions. Read it before making
any git changes. Preserve all user-owned work, inspect status and diffs before
staging, create or continue appropriate codex/... topic branches, make coherent
progress commits for safe intentional changes, run targeted verification from
docs/agent-test-map.md when code changed, and push maintained branches to origin.

Never force-push, reset, clean, or stage private, generated, credential, cache,
virtualenv, or local state files. If the worktree is ambiguous, risky, conflicted,
or verification fails in a relevant way, leave the worktree intact and report the
blocker instead of committing.

Finish with branch names, commit hashes, tests run, files intentionally left
uncommitted, and any push or verification failures.
"@

$codexArgs = @(
    "exec",
    "--cd",
    $repoRoot.Path,
    "--sandbox",
    "danger-full-access",
    "--ask-for-approval",
    "never",
    $prompt
)

if ($DryRun) {
    "Codex CLI: $codexCli"
    "Repo root: $($repoRoot.Path)"
    "Log path: $logPath"
    "Arguments:"
    $codexArgs
    exit 0
}

& $codexCli @codexArgs *>&1 | Tee-Object -FilePath $logPath
exit $LASTEXITCODE
