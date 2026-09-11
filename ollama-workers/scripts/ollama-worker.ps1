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
must be a linked git worktree - see Test-LinkedWorktree.

-Probe answers "would a dispatch into -Cwd get past the preflight below?"
without launching anything: it runs Get-PreflightBlocker, the same function in
the same order the dispatch path runs, and prints the first blocker as JSON. `enabled` is reported, not enforced, so a probe
works while workers are off and `on` can check the directory it is enabling
for. It exists because those checks were previously reachable only by
dispatching, which made a whole class of misconfiguration silent: an
orchestrator that read the worktree rule and correctly routed around it
produced no error, no log line, and no signal that the feature was inert.

Exit codes: 0 done (probe: dispatchable), 2 escalate to an Anthropic
implementer, 1 wrapper error (probe: not dispatchable).
#>
[CmdletBinding()]
param(
    [string]$BriefFile,
    [string]$Cwd,
    [string]$Resume,
    [string]$Model,
    [int]$MaxTurns,
    [string]$Label,
    [switch]$DryRun,
    [switch]$Probe
)

$ErrorActionPreference = 'Stop'

# OLLAMA_WORKERS_HOME exists for the tests: without it every test run reads the
# real state file and appends rows to the real run log, which is then read for
# calibration as if they were dispatches.
$claudeHome = if ($env:OLLAMA_WORKERS_HOME) { $env:OLLAMA_WORKERS_HOME } else { Join-Path $HOME '.claude' }
$statePath  = Join-Path $claudeHome 'ollama-workers.json'
$overlay    = Join-Path $claudeHome 'ollama-settings.json'
$logPath    = Join-Path $claudeHome 'ollama-workers.log.jsonl'
$workerCfg  = Join-Path $HOME '.claude-ollama-worker'

$worktreeHelp = "  Create one with: git worktree add <path> <branch>"

function Fail([string]$message, [int]$code = 1) {
    [Console]::Error.WriteLine("ollama-worker: $message")
    exit $code
}

function Write-LogRow([System.Collections.IDictionary]$row) {
    $line = $row | ConvertTo-Json -Depth 4 -Compress
    # Retried, not just caught: dozens of sessions append here concurrently, and a
    # dropped start or run row reads later as a run that never happened or a kill
    # that never happened.
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try { Add-Content -LiteralPath $logPath -Value $line -ErrorAction Stop; return }
        catch { if ($attempt -lt 3) { Start-Sleep -Milliseconds (50 * $attempt) } }
    }
    [Console]::Error.WriteLine("ollama-worker: could not append to $logPath")
}

function Get-OllamaPath {
    $p = (Get-Command ollama -ErrorAction SilentlyContinue).Source
    if (-not $p) { $p = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe' }
    if ($p -and (Test-Path -LiteralPath $p)) { return $p }
    return $null
}

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
#
# Called only from Get-PreflightBlocker below, which is itself the dispatch
# path's and -Probe's only preflight. They have to agree - a probe that says
# DISPATCHABLE where a dispatch then fails would restore the silence -Probe
# exists to break.
function Test-LinkedWorktree([string]$path) {
    $dirs = @()
    $code = 1
    try {
        $dirs = @(& git -C $path rev-parse --path-format=absolute --git-dir --git-common-dir 2>$null)
        $code = $LASTEXITCODE
    }
    catch { $code = 1 }

    if ($code -ne 0 -or $dirs.Count -lt 2) {
        return @{ ok = $false; reason = 'not a git worktree'; gitDir = $null; gitCommonDir = $null }
    }
    if ([string]::Equals($dirs[0], $dirs[1], [StringComparison]::OrdinalIgnoreCase)) {
        return @{ ok = $false; reason = 'primary checkout'; gitDir = $dirs[0]; gitCommonDir = $dirs[1] }
    }
    return @{ ok = $true; reason = ''; gitDir = $dirs[0]; gitCommonDir = $dirs[1] }
}

# Required for a dispatch, but not [Parameter(Mandatory)] and not checked under
# -Probe: a mandatory parameter prompts, and this script is only ever run
# headless, where a prompt hangs until timeout. A probe has no brief.
if (-not $Probe) {
    if (-not $BriefFile) { Fail '-BriefFile is required' }
    if (-not (Test-Path -LiteralPath $BriefFile)) { Fail "brief file not found: $BriefFile" }
}

$state = @{}
if (Test-Path -LiteralPath $statePath) {
    try { $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json -AsHashtable }
    catch { Fail "state file is not valid JSON: $statePath" }
}

# Resolved above the enabled gate because -Probe reports them while workers are
# off. Both are pure reads of $state; nothing acts on either until after the
# gate. -as [int], not a cast: a non-numeric maxTurns would otherwise throw a
# terminating error here and take a probe down before it could report why.
if (-not $Model) { $Model = if ($state.model) { $state.model } else { 'glm-5.3-flash:cloud' } }
if (-not $PSBoundParameters.ContainsKey('MaxTurns')) {
    $stateTurns = $state.maxTurns -as [int]
    $MaxTurns = if ($stateTurns) { $stateTurns } else { 25 }
}
$modelSyntaxOk = $Model -match '^[A-Za-z0-9][A-Za-z0-9._:/-]*$'

# The dispatch preflight, in one place, returning the first blocker instead of
# exiting on it. Both callers run this and nothing else: the dispatch path
# fails with .fail, -Probe reports .reason and .remedy.
#
# It is one function rather than two lists kept in step because the first cut
# of this fix gave -Probe its own copy of the checks, in a different order,
# under a comment asserting the orders matched. A directory that was both a
# primary checkout and carried a malformed model tag was told to create a
# worktree; the dispatch that followed still died on the tag, which the probe
# had never mentioned. A caller that cannot see the order cannot disagree with
# it.
#
# .fail is verbatim what a rejected dispatch has always printed; .remedy is the
# short form the status hook shows. $Resume is empty for every probe caller, so
# including it costs a probe nothing and leaves the order no exceptions.
# -BriefFile is deliberately absent: a missing brief is a fact about the
# caller's request, not about whether this directory can be dispatched into,
# and a probe has no brief.
function Get-PreflightBlocker([string]$path) {
    $r = [ordered]@{
        ok     = $false
        reason = ''
        remedy = ''
        fail   = ''
        wt     = @{ ok = $false; reason = ''; gitDir = $null; gitCommonDir = $null }
        ollama = $null
    }

    # $Model and $Resume are the only values that reach the child's command
    # line from outside this script - $Cwd and $BriefFile travel as
    # -WorkingDirectory and -RedirectStandardInput, and $overlay derives from
    # $HOME. Both are checked against an allowlist here rather than only
    # escaped below, because escaping is one layer and a mistake in it is
    # silent, whereas a rejected tag is loud. Both callers reach this after the
    # state read on purpose: a poisoned `model` key must fail too, not just a
    # poisoned -Model.
    #
    # The requirement is not the exact ollama grammar - it is "no whitespace,
    # no quote, no backslash, no cmd metacharacter, and not a leading dash".
    # Real cloud tags (glm-5.3-flash:cloud, hf.co/user/model:tag) fit. A
    # session id is looser than ^uuid$ deliberately: the CLI emits canonical
    # UUIDs today (verified 5e08f631-daaf-40ab-8bfa-d5c3f40ace37), and a
    # charset check blocks every injection character without breaking if that
    # format ever changes.
    if (-not $modelSyntaxOk) {
        $r.reason = "model tag is not dispatchable: $Model"
        $r.remedy = '  Set a usable tag with: /ollama-workers on <model>'
        $r.fail   = "model tag has characters that are not allowed on a command line: $Model"
        return $r
    }
    if ($Resume -and $Resume -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') {
        $r.reason = "resume id is not dispatchable: $Resume"
        $r.remedy = '  Pass -Resume a session id from an earlier run, or omit it.'
        $r.fail   = "resume id has characters that are not allowed on a command line: $Resume"
        return $r
    }

    # Required, but not [Parameter(Mandatory)]: a mandatory parameter prompts,
    # and this script is only ever run headless, where a prompt hangs until
    # timeout. -Probe defaults $Cwd before it calls this, so the check is not
    # skipped for a probe - it passes.
    if (-not $path) {
        $r.reason = 'cwd not set'
        $r.remedy = '  Pass -Cwd a linked git worktree.'
        $r.fail   = '-Cwd is required (a linked git worktree)'
        return $r
    }
    if (-not (Test-Path -LiteralPath $path)) {
        $r.reason = 'cwd not found'
        $r.remedy = '  Pass -Cwd a directory that exists.'
        $r.fail   = "cwd not found: $path"
        return $r
    }
    if (-not (Test-Path -LiteralPath $overlay)) {
        $r.reason = 'settings overlay not found'
        $r.remedy = "  Re-run ollama-workers/install.ps1 to seed $overlay"
        $r.fail   = "settings overlay not found: $overlay"
        return $r
    }

    # See Test-LinkedWorktree above for why this is checked and not trusted.
    # The .fail messages stay verbatim: they are what a rejected dispatch has
    # always shown the caller.
    $r.wt = Test-LinkedWorktree $path
    if (-not $r.wt.ok) {
        $r.reason = $r.wt.reason
        $r.remedy = $worktreeHelp
        $r.fail   = if ($r.wt.reason -eq 'primary checkout') {
            "-Cwd is a primary checkout, not a linked worktree: $path`n  The worker runs with --dangerously-skip-permissions and refuses to edit a`n  primary checkout.`n$worktreeHelp"
        }
        else {
            "-Cwd is not a git worktree: $path`n  The worker runs with --dangerously-skip-permissions and only accepts one.`n$worktreeHelp"
        }
        return $r
    }

    $r.ollama = Get-OllamaPath
    if (-not $r.ollama) {
        $r.reason = 'ollama executable not found'
        $r.remedy = '  Install Ollama, then: ollama signin'
        $r.fail   = 'ollama executable not found on PATH or in LOCALAPPDATA'
        return $r
    }

    $r.ok = $true
    return $r
}

# -Probe: report whether a dispatch into $Cwd would clear the preflight,
# without launching anything. Deliberately above the enabled gate - `on` probes
# the directory it is about to enable for, and `status` has to be able to say
# "enabled, but not dispatchable here", which is precisely the state that used
# to look healthy from every angle while no dispatch could ever succeed.
#
# The verdict is Get-PreflightBlocker's, so it names the blocker a dispatch
# would hit first - not a different one that also happens to be true. `enabled`
# is reported beside the verdict rather than folded into it: it is a switch the
# user owns, while everything in reason is a fact about this directory or
# install that turning the switch on will not change.
#
# Only the first blocker is reported, so git_dir and git_common_dir are null
# when an earlier check returned before the worktree test ran. The probe
# answers "what fails first", which is what the caller has to fix first.
if ($Probe) {
    if (-not $Cwd) { $Cwd = (Get-Location).Path }

    $pf = Get-PreflightBlocker $Cwd

    [ordered]@{
        dispatchable    = $pf.ok
        reason          = $pf.reason
        remedy          = $pf.remedy
        cwd             = $Cwd
        git_dir         = $pf.wt.gitDir
        git_common_dir  = $pf.wt.gitCommonDir
        enabled         = ($state.enabled -eq $true)
        model           = $Model
        model_syntax_ok = [bool]$modelSyntaxOk
        max_turns       = $MaxTurns
    } | ConvertTo-Json -Depth 4 -Compress

    # The availability row calibration was missing. A skipped dispatch writes
    # nothing, so ~/.claude/ollama-workers.log.jsonl could not tell "the worker
    # was never usable in this repo" from "no task was a good fit" - and any
    # retuning read out of it was drawing on data the failure mode deletes.
    # Logged only when workers are ON and the answer is no: a yes teaches
    # calibration nothing, and a probe while off is the user checking a switch,
    # not a dispatch that was lost. event='probe' keeps these rows out of the
    # turn and escalation statistics, which are about runs.
    if (($state.enabled -eq $true) -and -not $pf.ok) {
        Write-LogRow ([ordered]@{
            ts           = (Get-Date).ToUniversalTime().ToString('o')
            event        = 'probe'
            label        = $Label
            cwd          = $Cwd
            model        = $Model
            dispatchable = $false
            reason       = $pf.reason
        })
    }

    if ($pf.ok) { exit 0 } else { exit 1 }
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

# The same preflight -Probe reports on, so a DISPATCHABLE verdict and a
# dispatch that survives its checks are the same fact, and a probe can never
# name a blocker other than the one this exits on.
$pf = Get-PreflightBlocker $Cwd
if (-not $pf.ok) { Fail $pf.fail }
$ollama = $pf.ollama

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

# The start row is written before the launch because the run row cannot be
# relied on: it is written after the child exits, so anything that kills this
# process tree first - Claude Code stops background commands when the machine
# runs low on memory, and when the agent that launched them ends its turn -
# leaves no run row, no verdict and no exit 2. Measured: 7 of 29 real worker
# runs had no row at all. A start row with no run row sharing its run_id is a
# killed run. The same id goes to stderr now, so a caller holding only the
# killed command's output can still name the run.
$runId = [guid]::NewGuid().ToString()
Write-LogRow ([ordered]@{
    ts          = (Get-Date).ToUniversalTime().ToString('o')
    event       = 'start'
    run_id      = $runId
    label       = $Label
    cwd         = $Cwd
    model       = $Model
    resumed     = [bool]$Resume
    wrapper_pid = $PID
})
[Console]::Error.WriteLine("ollama-worker: run_id=$runId started (label '$Label', model $Model)")

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
        run_id = $runId
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
        run_id      = $runId
    }
}

# Logged BEFORE the verdict is printed. A caller may treat "a run row with this
# verdict's run_id exists" as proof the wrapper really ran (a forwarder that did
# the task itself returns no such row), and it may check the moment the verdict
# appears - so the row has to be on disk first.
$logEntry = [ordered]@{
    ts          = (Get-Date).ToUniversalTime().ToString('o')
    event       = 'run'
    run_id      = $runId
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
Write-LogRow $logEntry

$verdict | ConvertTo-Json -Depth 6 -Compress

if ($escalate) { exit 2 } else { exit 0 }
