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

Every dispatch is bounded three ways: --max-turns on the headless run, a wall
clock limit (timeoutMinutes) after which the whole worker process tree is
killed, and a cap on how many workers run at once (maxConcurrent). See the
comments at each for the evidence behind them.

-Await answers "has the dispatch whose background output is in this file
finished?" for a forwarder that launched the wrapper in the background. It
waits at most -PollSeconds and always ends by the wrapper's own time limit plus
-GraceSeconds, whatever state the wrapper is in.

Exit codes: 0 done (probe: dispatchable; await: finished or waiting), 2
escalate to an Anthropic implementer, 1 wrapper error (probe: not dispatchable;
await: no verdict).
#>
[CmdletBinding()]
param(
    [string]$BriefFile,
    [string]$Cwd,
    [string]$Resume,
    [string]$Model,
    [int]$MaxTurns,
    [double]$TimeoutMinutes,
    [string]$Label,
    [switch]$DryRun,
    [switch]$Probe,
    [string]$Await,
    [int]$PollSeconds = 240,
    [int]$GraceSeconds = 180
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
    # Retried, not just caught: sessions append here concurrently, and a dropped
    # start or run row reads later as a run that never happened or a kill that
    # never happened.
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
# -Probe or -Await: a mandatory parameter prompts, and this script is only ever
# run headless, where a prompt hangs until timeout. Neither has a brief.
if (-not $Probe -and -not $Await) {
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

# 25 minutes: the longest successful run in ~/.claude/ollama-workers.log.jsonl
# (58 of them, 2026-09-08 to 09-14) took 19.5 minutes by duration_ms, p95 8.2,
# median 2.2, and wall time from start row to run row never exceeded
# duration_ms by more than 0.6 minutes. The limit kills nothing that has ever
# succeeded. [double], so the tests can use a few seconds.
if (-not $PSBoundParameters.ContainsKey('TimeoutMinutes')) {
    $stateTimeout = $state.timeoutMinutes -as [double]
    $TimeoutMinutes = if ($stateTimeout -gt 0) { $stateTimeout } else { 25 }
}
if ($TimeoutMinutes -le 0) { $TimeoutMinutes = 25 }
$timeoutLabel = $TimeoutMinutes.ToString([System.Globalization.CultureInfo]::InvariantCulture)
$timeoutSeconds = [int][math]::Ceiling($TimeoutMinutes * 60)

# 1 by default: each worker is a pwsh wrapper plus a full headless Claude Code
# process on a machine where background tasks were already being killed for low
# memory. Zero, negative or non-numeric falls back to 1 rather than to "no
# workers", because `enabled` is the off switch.
$stateConcurrent = $state.maxConcurrent -as [int]
$MaxConcurrent = if ($stateConcurrent -gt 0) { $stateConcurrent } else { 1 }
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
        timeout_minutes = $TimeoutMinutes
        max_concurrent  = $MaxConcurrent
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

# -Await: the forwarder's wait, bounded here rather than in its prompt.
#
# The forwarder launches the wrapper as a background command and has to hand its
# caller one verdict. Ending its turn and being woken when the command finishes
# does keep the command alive (verified 2026-09-17), but the caller receives the
# forwarder's first reply - "launched" - as an interim result, not the verdict.
# So the forwarder waits in the foreground, and each wait is one call to this.
#
# The old forwarder looped "until STATE: finished" with no upper bound, and a
# wrapper blocked for 8h10m got polled 35 times. Every exit from this block is
# decided from facts the wrapper wrote, not from how often the model has asked:
#
# - a verdict line in the output: finished.
# - the wrapper named in the start line is gone with no verdict: finished, with
#   a synthesized escalate verdict, because a wrapper killed from outside (low
#   memory, a user interrupt) prints nothing. Liveness is the PID plus a start
#   time no later than the start line's, so a reused PID reads as gone.
# - past the start line's timeout plus -GraceSeconds: the wrapper failed to
#   enforce its own limit. Kill it - its job object takes the worker tree with
#   it - and return an escalate verdict, so the caller never re-dispatches the
#   task while a worker is still editing the same worktree.
# - no start line and the harness recorded an exit: the wrapper refused before
#   launching (workers off, bad -Cwd). There is no verdict to return.
#
# -PollSeconds defaults to 240 so each wait returns inside the forwarder's
# 5-minute prompt cache; 9-minute polls re-sent the whole context on every wake.
# Deliberately above the enabled gate: turning workers off must not strand a
# forwarder whose worker is already running.
if ($Await) {
    function Read-AwaitText {
        if (Test-Path -LiteralPath $Await) { Get-Content -Raw -LiteralPath $Await -ErrorAction SilentlyContinue } else { '' }
    }
    function Get-VerdictLine([string]$text) {
        @("$text" -split '\r?\n' | Where-Object { $_.TrimStart().StartsWith('{"ok"') }) | Select-Object -Last 1
    }
    function Write-Finished([string]$verdictLine, [string]$text) {
        'STATE: finished'
        $verdictLine.Trim()
        "$text" -split '\r?\n' | Where-Object { $_ -like 'ollama-worker:*' -and $_ -notmatch ' started \(' }
        exit 0
    }
    function Test-WrapperAlive([int]$wrapperPid, [datetime]$at) {
        $p = Get-Process -Id $wrapperPid -ErrorAction SilentlyContinue
        if (-not $p) { return $false }
        # Unreadable start time means a process this user does not own, which
        # the wrapper never is - and a PID that fails the check is never killed.
        try { return $p.StartTime.ToUniversalTime() -le $at.AddSeconds(2) }
        catch { return $false }
    }
    # A run row for a wrapper that cannot write its own, unless it managed to.
    # Without it a killed run is visible only as a start row with no partner.
    function Complete-Run([hashtable]$s, [string]$reason) {
        $rows = @()
        if (Test-Path -LiteralPath $logPath) {
            $rows = @(Get-Content -LiteralPath $logPath | ForEach-Object {
                try { $_ | ConvertFrom-Json -AsHashtable } catch { $null }
            } | Where-Object { $_ -and $_.run_id -eq $s.runId })
        }
        $existing = $rows | Where-Object { $_.event -eq 'run' } | Select-Object -Last 1
        if ($existing) {
            # The wrapper finished and logged, but its verdict line never reached
            # the output file. Report what it logged.
            $v = [ordered]@{
                ok = (-not $existing.escalate); escalate = [bool]$existing.escalate; reason = $existing.reason
                model = $existing.model; session_id = $existing.session_id; num_turns = $existing.num_turns
                duration_ms = $existing.duration_ms; result = $null; run_id = $s.runId
            }
        }
        else {
            $v = [ordered]@{
                ok = $false; escalate = $true; reason = $reason; model = $s.model
                session_id = $null; num_turns = 0; duration_ms = 0; result = $null; run_id = $s.runId
            }
            $startRow = $rows | Where-Object { $_.event -eq 'start' } | Select-Object -First 1
            Write-LogRow ([ordered]@{
                ts          = (Get-Date).ToUniversalTime().ToString('o')
                event       = 'run'
                run_id      = $s.runId
                label       = $s.label
                cwd         = if ($startRow) { $startRow.cwd } else { $null }
                model       = $s.model
                resumed     = if ($startRow) { [bool]$startRow.resumed } else { $false }
                session_id  = $null
                num_turns   = 0
                duration_ms = 0
                escalate    = $true
                reason      = $reason
                recorded_by = 'await'
            })
        }
        return ($v | ConvertTo-Json -Depth 4 -Compress)
    }

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $startPattern = "ollama-worker: run_id=(?<id>[A-Za-z0-9-]+) started \(label '(?<label>.*?)', model (?<model>\S+), wrapper_pid (?<pid>\d+), timeout_s (?<t>\d+), at (?<at>[^)\s]+)\)"
    while ($true) {
        $text = Read-AwaitText
        $line = Get-VerdictLine $text
        if ($line) { Write-Finished $line $text }

        $m = [regex]::Match("$text", $startPattern)
        $exited = [regex]::Match("$text", '\[exited with code (?<c>-?\d+)\]')
        if (-not $m.Success) {
            $age = if (Test-Path -LiteralPath $Await) { ((Get-Date) - (Get-Item -LiteralPath $Await).CreationTime).TotalSeconds } else { 0 }
            if ($exited.Success -or $age -gt 120) {
                'STATE: no verdict'
                "$text".Trim()
                exit 1
            }
        }
        else {
            $s = @{
                runId = $m.Groups['id'].Value; label = $m.Groups['label'].Value; model = $m.Groups['model'].Value
                pid = [int]$m.Groups['pid'].Value; timeoutS = [int]$m.Groups['t'].Value
                at = [datetime]::Parse($m.Groups['at'].Value, [System.Globalization.CultureInfo]::InvariantCulture,
                    [System.Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime()
            }
            if (-not (Test-WrapperAlive $s.pid $s.at)) {
                # The harness copies output to the file after the process ends,
                # so a verdict can land a moment after the PID is gone.
                for ($i = 0; $i -lt 10; $i++) {
                    Start-Sleep -Milliseconds 500
                    $text = Read-AwaitText
                    $line = Get-VerdictLine $text
                    if ($line) { Write-Finished $line $text }
                }
                $exited = [regex]::Match("$text", '\[exited with code (?<c>-?\d+)\]')
                $reason = if ($exited.Success) { "wrapper_exit_$($exited.Groups['c'].Value)" } else { 'wrapper_died' }
                Write-Finished (Complete-Run $s $reason) $text
            }
            if ((Get-Date).ToUniversalTime() -gt $s.at.AddSeconds($s.timeoutS + $GraceSeconds)) {
                & taskkill /F /T /PID $s.pid 2>&1 | Out-Null
                $text = Read-AwaitText
                $line = Get-VerdictLine $text
                if ($line) { Write-Finished $line $text }
                Write-Finished (Complete-Run $s 'wrapper_overdue') $text
            }
        }

        $left = $PollSeconds - $sw.Elapsed.TotalSeconds
        if ($left -le 0) { 'STATE: waiting'; exit 0 }
        Start-Sleep -Milliseconds ([int][math]::Min(5000, [math]::Max(100, $left * 1000)))
    }
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

# --max-turns is a hard stop, not the after-the-fact comparison below: 14 runs
# went 27 to 86 turns, up to 17 minutes, before being discarded. Verified
# 2026-09-17 on Claude Code 2.1.274 through ollama 0.34.0: `ollama launch
# claude ... -- --max-turns 1` passes the flag through, and a capped run ends
# with type "result", subtype "error_max_turns", is_error true, exit 1, and
# num_turns one ABOVE the cap (2 for a cap of 1).
$claudeArgs = @('--settings', $overlay, '-p', '--output-format', 'json', '--max-turns', "$MaxTurns", '--dangerously-skip-permissions')
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
        timeoutMinutes = $TimeoutMinutes
        maxConcurrent  = $MaxConcurrent
        brief     = $BriefFile
    } | ConvertTo-Json -Depth 4
    exit 0
}

# Concurrency cap: one named mutex per slot, held until this process exits.
#
# A crashed wrapper must not keep a slot, and counting from the log cannot see
# one: a wrapper killed from outside writes a start row and never a run row
# (hero-task-11). The kernel releases a mutex when its owner dies - verified
# 2026-09-17, a slot held by a process killed with Stop-Process was free the
# next instant - so there is no lock file to go stale and no PID to be reused.
# A named semaphore would not do: its count is not given back when a holder
# dies. And a live wrapper really is a live worker, because the job object
# below kills the worker tree when the wrapper goes, however it goes.
#
# The name carries a hash of $claudeHome, so the tests' OLLAMA_WORKERS_HOME
# never competes with real workers for a slot. Local\ is this logon session,
# which is every Claude Code session on the desktop.
#
# Refused rather than queued: a queue is one more place to wait without bound.
# The refusal is logged as event "refused", not "run", so it stays out of turn
# and escalation statistics - nothing ran.
$homeKey = [Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData(
    [System.Text.Encoding]::UTF8.GetBytes($claudeHome.ToLowerInvariant()))).Substring(0, 16)
$slot = $null
for ($i = 0; $i -lt $MaxConcurrent -and -not $slot; $i++) {
    $m = [System.Threading.Mutex]::new($false, "Local\ollama-worker-$homeKey-slot-$i")
    try {
        if ($m.WaitOne(0)) { $slot = $m } else { $m.Dispose() }
    }
    catch {
        # Abandoned: another process still had the mutex open when its holder
        # died. The slot is ours now.
        $e = $_.Exception
        while ($e -and $e -isnot [System.Threading.AbandonedMutexException]) { $e = $e.InnerException }
        if ($e) { $slot = $m } else { throw }
    }
}
if (-not $slot) {
    Write-LogRow ([ordered]@{
        ts             = (Get-Date).ToUniversalTime().ToString('o')
        event          = 'refused'
        label          = $Label
        cwd            = $Cwd
        model          = $Model
        reason         = 'concurrency_cap'
        max_concurrent = $MaxConcurrent
    })
    [ordered]@{
        ok = $false; escalate = $true; reason = 'concurrency_cap'; model = $Model
        session_id = $null; num_turns = 0; duration_ms = 0; result = $null; run_id = $null
    } | ConvertTo-Json -Depth 4 -Compress
    exit 2
}

# The start row is written before the launch because the run row cannot be
# relied on: anything that kills this process first - Claude Code stops
# background commands when the machine runs low on memory, a user interrupts -
# leaves no run row and no verdict. A start row with no run row sharing its
# run_id is a killed run. The same facts go to stderr: -Await parses that line
# to find this process and its deadline, so its format is a contract.
$runId = [guid]::NewGuid().ToString()
$startedAt = (Get-Date).ToUniversalTime().ToString('o')
Write-LogRow ([ordered]@{
    ts              = $startedAt
    event           = 'start'
    run_id          = $runId
    label           = $Label
    cwd             = $Cwd
    model           = $Model
    resumed         = [bool]$Resume
    wrapper_pid     = $PID
    max_turns       = $MaxTurns
    timeout_minutes = $TimeoutMinutes
})
[Console]::Error.WriteLine("ollama-worker: run_id=$runId started (label '$Label', model $Model, wrapper_pid $PID, timeout_s $timeoutSeconds, at $startedAt)")

$stdoutFile = [System.IO.Path]::GetTempFileName()
$stderrFile = [System.IO.Path]::GetTempFileName()
$exitCode = $null
$timedOut = $false
$leftover = $null
$wrapperError = $null
$clock = [System.Diagnostics.Stopwatch]::StartNew()

# Everything from here to the verdict is inside one try, so any failure still
# ends in a verdict and a run row rather than a bare non-zero exit.
try {
    # The worker's whole process tree goes in a job object, and the wait is on
    # the launcher alone.
    #
    # hero-task-11 (2026-09-12) is why. The worker ended its turn at 17:47:19Z
    # and wrote a complete result envelope to stdout, yet the wrapper returned
    # only when a user interrupt killed it at 01:42. `Start-Process -Wait`
    # waits for every descendant, not just the process it started - verified on
    # pwsh 7.6.6: a parent that exited at once, leaving a 25-second grandchild,
    # returned after 26.1s - and that worker had started a static server from a
    # throwaway puppeteer probe (spawn with shell: true), plus a prerender
    # build. Which process outlived it is not recorded; the wait semantics are.
    #
    # Killing the launcher is not enough either. Verified with a real `ollama
    # launch claude`: after Stop-Process on ollama.exe, claude.exe and its bash
    # children kept running, and `taskkill /T` on the launcher PID then found
    # nothing, because the tree is walked through parent PIDs and the parent
    # was gone. TerminateJobObject killed all of them. KILL_ON_JOB_CLOSE does the
    # same when this wrapper dies without reaching the end, so a harness kill of
    # the wrapper no longer leaves a worker running with nobody waiting on it.
    #
    # Compiled here, after -Probe and -DryRun have exited, so they do not pay
    # for it.
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class OllamaWorkerJob {
    [StructLayout(LayoutKind.Sequential)]
    struct BasicLimit {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct IoCounters { public ulong R, W, O, RB, WB, OB; }
    [StructLayout(LayoutKind.Sequential)]
    struct ExtendedLimit {
        public BasicLimit Basic;
        public IoCounters Io;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct BasicAccounting {
        public long TotalUserTime, TotalKernelTime, ThisPeriodTotalUserTime, ThisPeriodTotalKernelTime;
        public uint TotalPageFaultCount, TotalProcesses, ActiveProcesses, TotalTerminatedProcesses;
    }

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern IntPtr CreateJobObjectW(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref ExtendedLimit info, uint length);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool QueryInformationJobObject(IntPtr job, int infoClass, out BasicAccounting info, uint length, IntPtr returned);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool TerminateJobObject(IntPtr job, uint exitCode);

    // The handle is never closed explicitly: process exit closes it, and
    // KILL_ON_JOB_CLOSE then ends whatever is still in the job. It is not
    // inheritable, so no child can hold the job open after this process dies.
    public static IntPtr Create() {
        IntPtr job = CreateJobObjectW(IntPtr.Zero, null);
        if (job == IntPtr.Zero) throw new System.ComponentModel.Win32Exception();
        var info = new ExtendedLimit();
        info.Basic.LimitFlags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (!SetInformationJobObject(job, 9, ref info, (uint)Marshal.SizeOf(typeof(ExtendedLimit))))
            throw new System.ComponentModel.Win32Exception();
        return job;
    }
    public static bool TryAssign(IntPtr job, IntPtr process) { return AssignProcessToJobObject(job, process); }
    public static int Active(IntPtr job) {
        BasicAccounting a;
        if (!QueryInformationJobObject(job, 1, out a, (uint)Marshal.SizeOf(typeof(BasicAccounting)), IntPtr.Zero)) return -1;
        return (int)a.ActiveProcesses;
    }
    public static bool Terminate(IntPtr job) { return TerminateJobObject(job, 1); }
}
'@
    $job = [OllamaWorkerJob]::Create()

    $hadConfigDir = Test-Path Env:\CLAUDE_CONFIG_DIR
    $prevConfigDir = if ($hadConfigDir) { $env:CLAUDE_CONFIG_DIR } else { $null }
    $env:CLAUDE_CONFIG_DIR = $workerCfg
    try {
        $proc = Start-Process -FilePath $ollama -ArgumentList $quoted -WorkingDirectory $Cwd `
            -RedirectStandardInput $BriefFile -RedirectStandardOutput $stdoutFile `
            -RedirectStandardError $stderrFile -NoNewWindow -PassThru
    }
    finally {
        if ($hadConfigDir) { $env:CLAUDE_CONFIG_DIR = $prevConfigDir }
        else { Remove-Item Env:\CLAUDE_CONFIG_DIR -ErrorAction SilentlyContinue }
    }

    # Assigned right after the start. The launcher only spawns claude.exe once
    # it has read its own configuration, so its children are born in the job.
    $inJob = [OllamaWorkerJob]::TryAssign($job, $proc.Handle)
    if (-not $inJob) {
        [Console]::Error.WriteLine("ollama-worker: could not put the worker in a job object; a timeout falls back to taskkill /T")
    }

    $timeoutMs = [int][math]::Min([int]::MaxValue, [math]::Ceiling($TimeoutMinutes * 60000))
    $timedOut = -not $proc.WaitForExit($timeoutMs)

    if ($timedOut) {
        # The tree is still connected while the launcher lives, so /T reaches
        # anything that escaped the job; the job reaches what /T cannot.
        & taskkill /F /T /PID $proc.Id 2>&1 | Out-Null
    }
    else {
        $exitCode = $proc.ExitCode
    }
    if ($inJob) {
        # After a normal exit this counts what the worker left running - a dev
        # server, a watch-mode test - and kills it.
        $leftover = [OllamaWorkerJob]::Active($job)
        [void][OllamaWorkerJob]::Terminate($job)
    }
}
catch {
    $wrapperError = $_.Exception.Message
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
if ($resultLine -and -not $timedOut -and -not $wrapperError) {
    try { $r = $resultLine | ConvertFrom-Json }
    catch { $r = $null }
    if ($r.type -ne 'result') { $r = $null }
}

$escalate = $false
$reason   = ''
$verdict  = [ordered]@{}

if ($wrapperError -or $timedOut -or $null -eq $r) {
    $escalate = $true
    $reason   = if ($wrapperError) { 'wrapper_error' }
                elseif ($timedOut) { "timeout_${timeoutLabel}m" }
                elseif ($resultLine) { "invalid_result_json_exit_$exitCode" }
                else { "no_result_json_exit_$exitCode" }
    $verdict  = [ordered]@{
        ok = $false; escalate = $true; reason = $reason; model = $Model
        session_id = $null; num_turns = 0; duration_ms = [int]$clock.ElapsedMilliseconds
        result = $wrapperError; run_id = $runId
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

    # The cap first: a capped run is also is_error and exits 1, and either of
    # those reasons would hide why it stopped. The num_turns comparison stays as
    # a second layer for a CLI that ignores --max-turns.
    if ($r.subtype -eq 'error_max_turns') { $escalate = $true; $reason = "max_turns_$MaxTurns" }
    elseif ($r.is_error)          { $escalate = $true; $reason = 'is_error' }
    elseif ($exitCode -ne 0)      { $escalate = $true; $reason = "exit_$exitCode" }
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

# Logged BEFORE the verdict is printed, so a caller that sees the verdict can
# rely on the row being on disk.
Write-LogRow ([ordered]@{
    ts                 = (Get-Date).ToUniversalTime().ToString('o')
    event              = 'run'
    run_id             = $runId
    label              = $Label
    cwd                = $Cwd
    model              = $Model
    resumed            = [bool]$Resume
    session_id         = $verdict.session_id
    num_turns          = $verdict.num_turns
    duration_ms        = $verdict.duration_ms
    wall_ms            = [int]$clock.ElapsedMilliseconds
    leftover_processes = $leftover
    escalate           = $verdict.escalate
    reason             = $verdict.reason
})

$verdict | ConvertTo-Json -Depth 6 -Compress

if ($escalate) { exit 2 } else { exit 0 }
