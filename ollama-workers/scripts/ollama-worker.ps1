#Requires -Version 7
<#
.SYNOPSIS
Runs one implementation task on an Ollama cloud model through a separate
headless Claude Code process, and reports a verdict the caller can route on.

.DESCRIPTION
`ollama launch claude` exports ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN and all
three ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL vars, so one process serves
exactly one model from one endpoint. That is why the worker is a child process
rather than a subagent: the orchestrator keeps its own Anthropic auth.

The worker runs under its own CLAUDE_CONFIG_DIR. Sessions live at
<config-dir>/projects/<cwd>/, so sharing the caller's config dir would leave
worker transcripts where the caller's next `claude --continue` would resume
them - a session produced by a non-Anthropic backend fails to resume against
the Anthropic API.

The worker runs with --dangerously-skip-permissions, so -Cwd is required and
must be a linked git worktree - see the guard below.

Exit codes: 0 done, 2 escalate to an Anthropic implementer, 1 wrapper error.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$BriefFile,
    [string]$Cwd,
    [string]$Resume,
    [string]$Model,
    [int]$MaxTurns,
    [string]$Label,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$claudeHome = Join-Path $HOME '.claude'
$statePath  = Join-Path $claudeHome 'ollama-workers.json'
$overlay    = Join-Path $claudeHome 'ollama-settings.json'
$logPath    = Join-Path $claudeHome 'ollama-workers.log.jsonl'
$workerCfg  = Join-Path $HOME '.claude-ollama-worker'

function Fail([string]$message, [int]$code = 1) {
    [Console]::Error.WriteLine("ollama-worker: $message")
    exit $code
}

if (-not (Test-Path -LiteralPath $BriefFile)) { Fail "brief file not found: $BriefFile" }

$state = @{}
if (Test-Path -LiteralPath $statePath) {
    try { $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json -AsHashtable }
    catch { Fail "state file is not valid JSON: $statePath" }
}

if (-not $Model)               { $Model = if ($state.model) { $state.model } else { 'glm-5.3-flash:cloud' } }
if (-not $PSBoundParameters.ContainsKey('MaxTurns')) {
    $MaxTurns = if ($state.maxTurns) { [int]$state.maxTurns } else { 25 }
}
# Required, but not [Parameter(Mandatory)]: a mandatory parameter prompts, and
# this script is only ever run headless, where a prompt hangs until timeout.
if (-not $Cwd) { Fail '-Cwd is required (a linked git worktree)' }
if (-not (Test-Path -LiteralPath $Cwd)) { Fail "cwd not found: $Cwd" }
if (-not (Test-Path -LiteralPath $overlay)) { Fail "settings overlay not found: $overlay" }

# The run below passes --dangerously-skip-permissions, so nothing will stop the
# worker from editing or deleting anything under $Cwd. $Cwd therefore has to be
# disposable, and that has to be checked mechanically: an orchestrator picks it
# programmatically for every dispatch, so a doc convention only holds until the
# first slip in brief-generation.
#
# git's own bookkeeping is the test. In a linked worktree --git-dir is
# <primary>/.git/worktrees/<name> while --git-common-dir is <primary>/.git; in a
# primary checkout the two are identical. Note this rejects plain directories
# too, which matters more than it looks: if any ancestor is a repo (a home
# directory under version control, say) a scratch path silently resolves to
# *that* repo, and the worker would be editing inside it.
#
# --path-format=absolute needs git >= 2.31. Older git errors out, and a
# non-zero exit or a throw both land in the same fail-closed branch.
$gitDirs = @()
$gitExit = 1
try {
    $gitDirs = @(& git -C $Cwd rev-parse --path-format=absolute --git-dir --git-common-dir 2>$null)
    $gitExit = $LASTEXITCODE
}
catch { $gitExit = 1 }

$worktreeHelp = "  Create one with: git worktree add <path> <branch>"
if ($gitExit -ne 0 -or $gitDirs.Count -lt 2) {
    Fail "-Cwd is not a git worktree: $Cwd`n  The worker runs with --dangerously-skip-permissions and only accepts one.`n$worktreeHelp"
}
if ([string]::Equals($gitDirs[0], $gitDirs[1], [StringComparison]::OrdinalIgnoreCase)) {
    Fail "-Cwd is a primary checkout, not a linked worktree: $Cwd`n  The worker runs with --dangerously-skip-permissions and refuses to edit a`n  primary checkout.`n$worktreeHelp"
}

$ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $ollama) { $ollama = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe' }
if (-not (Test-Path -LiteralPath $ollama)) { Fail 'ollama executable not found on PATH or in LOCALAPPDATA' }

# Isolated config dir, with plugins junctioned in so the worker sees the same
# skills (TDD, verification-before-completion) the brief refers to. A junction,
# not ln -s: under git-bash ln -s silently copies the directory.
if (-not (Test-Path -LiteralPath $workerCfg)) {
    New-Item -ItemType Directory -Path $workerCfg | Out-Null
}
$pluginLink = Join-Path $workerCfg 'plugins'
if (-not (Test-Path -LiteralPath $pluginLink)) {
    $pluginSrc = Join-Path $claudeHome 'plugins'
    if (Test-Path -LiteralPath $pluginSrc) {
        cmd /c mklink /J "$pluginLink" "$pluginSrc" | Out-Null
    }
}

$claudeArgs = @('--settings', $overlay, '-p', '--output-format', 'json', '--dangerously-skip-permissions')
if ($Resume) { $claudeArgs += @('--resume', $Resume) }
$argList = @('launch', 'claude', '--model', $Model, '--') + $claudeArgs

# Start-Process space-joins -ArgumentList without quoting, so quote here.
$quoted = $argList | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }

if ($DryRun) {
    [ordered]@{
        exe       = $ollama
        args      = $argList
        cwd       = $Cwd
        configDir = $workerCfg
        model     = $Model
        maxTurns  = $MaxTurns
        brief     = $BriefFile
    } | ConvertTo-Json -Depth 4
    exit 0
}

$stdoutFile = [System.IO.Path]::GetTempFileName()
$stderrFile = [System.IO.Path]::GetTempFileName()
$hadConfigDir = Test-Path Env:\CLAUDE_CONFIG_DIR
$prevConfigDir = if ($hadConfigDir) { $env:CLAUDE_CONFIG_DIR } else { $null }
$env:CLAUDE_CONFIG_DIR = $workerCfg

try {
    $proc = Start-Process -FilePath $ollama -ArgumentList $quoted -WorkingDirectory $Cwd `
        -RedirectStandardInput $BriefFile -RedirectStandardOutput $stdoutFile `
        -RedirectStandardError $stderrFile -NoNewWindow -PassThru -Wait
    $exitCode = $proc.ExitCode
}
finally {
    if ($hadConfigDir) { $env:CLAUDE_CONFIG_DIR = $prevConfigDir }
    else { Remove-Item Env:\CLAUDE_CONFIG_DIR -ErrorAction SilentlyContinue }
}

# The launcher prints warnings of its own; keep them on stderr so the caller
# parses only the verdict.
$stderrText = Get-Content -Raw -LiteralPath $stderrFile -ErrorAction SilentlyContinue
if ($stderrText) { [Console]::Error.Write($stderrText) }

$resultLine = Get-Content -LiteralPath $stdoutFile -ErrorAction SilentlyContinue |
    Where-Object { $_.TrimStart().StartsWith('{') } | Select-Object -Last 1
Remove-Item -LiteralPath $stdoutFile, $stderrFile -ErrorAction SilentlyContinue

$escalate = $false
$reason   = ''
$verdict  = [ordered]@{}

if (-not $resultLine) {
    $escalate = $true
    $reason   = "no_result_json_exit_$exitCode"
    $verdict  = [ordered]@{
        ok = $false; escalate = $true; reason = $reason; model = $Model
        session_id = $null; num_turns = 0; duration_ms = 0; result = $null
    }
}
else {
    $r = $resultLine | ConvertFrom-Json
    $turns = if ($null -ne $r.num_turns) { [int]$r.num_turns } else { 0 }
    if ($r.is_error)         { $escalate = $true; $reason = 'is_error' }
    elseif ($exitCode -ne 0) { $escalate = $true; $reason = "exit_$exitCode" }
    elseif ($turns -gt $MaxTurns) { $escalate = $true; $reason = "turns_${turns}_over_${MaxTurns}" }

    $verdict = [ordered]@{
        ok          = (-not $escalate)
        escalate    = $escalate
        reason      = $reason
        model       = $Model
        session_id  = $r.session_id
        num_turns   = $turns
        duration_ms = if ($null -ne $r.duration_ms) { [int]$r.duration_ms } else { 0 }
        result      = $r.result
    }
}

$verdict | ConvertTo-Json -Depth 6 -Compress

$logEntry = [ordered]@{
    ts          = (Get-Date).ToUniversalTime().ToString('o')
    label       = $Label
    cwd         = $Cwd
    model       = $Model
    resumed     = [bool]$Resume
    session_id  = $verdict.session_id
    num_turns   = $verdict.num_turns
    duration_ms = $verdict.duration_ms
    escalate    = $verdict.escalate
    reason      = $verdict.reason
}
try {
    Add-Content -LiteralPath $logPath -Value ($logEntry | ConvertTo-Json -Depth 4 -Compress)
}
catch {
    [Console]::Error.WriteLine("ollama-worker: could not append to $logPath")
}

if ($escalate) { exit 2 } else { exit 0 }
