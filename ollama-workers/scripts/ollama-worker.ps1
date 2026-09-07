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

# The off switch is enforced here, not only in the status hook and the skill's
# prose. An orchestrator can dispatch this agent on stale context after a
# /compact, from a careless caller, or from its own bug; every other constraint
# in this script is checked rather than trusted for exactly that reason. Running
# while off sends repository content to a third-party endpoint under
# --dangerously-skip-permissions, which is the one outcome `off` exists to
# prevent, so the gate is the mechanism's job and not the system prompt's.
#
# -ne $true with the state value on the left: PowerShell coerces the right side
# to the left's type, so a missing file, a missing key, null, false, "false", ""
# and 0 all fail closed, while true, "true" and 1 pass. Note a fresh install
# seeds enabled: false, so a new install fails here until `/ollama-workers on`.
# Exit 1, not 2: a dispatch while disabled is a caller bug the orchestrator
# should see, not an escalation the model earned, and not a calibration row.
if ($state.enabled -ne $true) {
    Fail "ollama workers are disabled in $statePath`n  Enable with: /ollama-workers on"
}

if (-not $Model)               { $Model = if ($state.model) { $state.model } else { 'glm-5.3-flash:cloud' } }
if (-not $PSBoundParameters.ContainsKey('MaxTurns')) {
    $MaxTurns = if ($state.maxTurns) { [int]$state.maxTurns } else { 25 }
}

# $Model and $Resume are the only values that reach the child's command line
# from outside this script - $Cwd and $BriefFile travel as -WorkingDirectory and
# -RedirectStandardInput, and $overlay derives from $HOME. Both are checked
# against an allowlist here rather than only escaped below, because escaping is
# one layer and a mistake in it is silent, whereas a rejected tag is loud. This
# is after the state read on purpose: a poisoned `model` key must fail too, not
# just a poisoned -Model.
#
# The requirement is not the exact ollama grammar - it is "no whitespace, no
# quote, no backslash, no cmd metacharacter, and not a leading dash". Real cloud
# tags (glm-5.3-flash:cloud, hf.co/user/model:tag) fit. A session id is looser
# than ^uuid$ deliberately: the CLI emits canonical UUIDs today (verified
# 5e08f631-daaf-40ab-8bfa-d5c3f40ace37), and a charset check blocks every
# injection character without breaking if that format ever changes.
if ($Model -notmatch '^[A-Za-z0-9][A-Za-z0-9._:/-]*$') {
    Fail "model tag has characters that are not allowed on a command line: $Model"
}
if ($Resume -and $Resume -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') {
    Fail "resume id has characters that are not allowed on a command line: $Resume"
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

# Start-Process space-joins -ArgumentList without quoting - verified: an
# argument containing a space arrives at the child split in two - so the line
# has to be built here, by the rules the CRT uses to take it apart again.
# Backslashes are literal except immediately before a quote, where they double;
# an embedded quote becomes \"; trailing backslashes double before the closing
# quote; an empty argument becomes "". The naive `wrap anything with a space in
# one quote pair` this replaces let a quote inside $Model or $Resume close the
# argument early, and the remainder became extra flags on a child launched with
# --dangerously-skip-permissions - after `--`, that is extra flags to claude
# itself. The allowlist above is what makes that unreachable; this function is
# the second layer, and the one that keeps a path with a space intact.
function QuoteArg([string]$a) {
    if ($a -and $a -notmatch '[\s"]') { return $a }
    $sb = [System.Text.StringBuilder]::new()
    [void]$sb.Append('"')
    for ($i = 0; $i -lt $a.Length; $i++) {
        $bs = 0
        while ($i -lt $a.Length -and $a[$i] -eq '\') { $bs++; $i++ }
        if ($i -ge $a.Length)   { [void]$sb.Append('\', $bs * 2); break }
        elseif ($a[$i] -eq '"') { [void]$sb.Append('\', $bs * 2 + 1); [void]$sb.Append('"') }
        else                    { [void]$sb.Append('\', $bs); [void]$sb.Append($a[$i]) }
    }
    [void]$sb.Append('"')
    return $sb.ToString()
}
# A List, not the pipeline: piping a one-element array yields a bare string and
# Start-Process would then see one argument's characters, not one argument.
$quoted = [System.Collections.Generic.List[string]]::new()
foreach ($a in $argList) { $quoted.Add((QuoteArg $a)) }

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

# The child's stdout is the one input the escalation design reads, so a bad line
# has to become a verdict, not a crash. $ErrorActionPreference is 'Stop' and
# ConvertFrom-Json throws on truncated JSON, so a child killed mid-write would
# otherwise take the wrapper down before it printed a verdict or logged the run,
# and the caller would see a bare non-zero exit instead of escalate: true. The
# type check covers the other shape failure: if the envelope is ever
# pretty-printed, the last line starting with '{' is a fragment, not the result.
$r = $null
if ($resultLine) {
    try { $r = $resultLine | ConvertFrom-Json }
    catch { $r = $null }
    if ($r.type -ne 'result') { $r = $null }
}

$escalate = $false
$reason   = ''
$verdict  = [ordered]@{}

if ($null -eq $r) {
    $escalate = $true
    $reason   = if ($resultLine) { "invalid_result_json_exit_$exitCode" }
                else             { "no_result_json_exit_$exitCode" }
    $verdict  = [ordered]@{
        ok = $false; escalate = $true; reason = $reason; model = $Model
        session_id = $null; num_turns = 0; duration_ms = 0; result = $null
    }
}
else {
    # -as, not [int]: casting a field that is not numeric throws a terminating
    # error $ErrorActionPreference cannot soften - the same crash the parse
    # guard above exists to prevent, two lines further down.
    $turns = $r.num_turns -as [int]
    if ($null -eq $turns) { $turns = 0 }
    $ms = $r.duration_ms -as [int]
    if ($null -eq $ms) { $ms = 0 }

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
        duration_ms = $ms
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
