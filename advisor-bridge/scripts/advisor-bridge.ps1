#Requires -Version 7
<#
.SYNOPSIS
Sends this Claude Code session's own transcript to an Anthropic model for
advice, from a session whose backend is not Anthropic.

.DESCRIPTION
`ollama launch claude` exports ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN and all
three ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL vars, so one process serves
exactly one model from one endpoint. The built-in advisor tool is disabled there
by the model catalog, and registering it would only get GLM advising GLM. So the
advisor is a child process with a scrubbed environment, pointed back at
Anthropic.

Exit codes: 0 advice returned, 1 the wrapper refused before spawning,
2 the call was attempted and its result is not trustworthy.
#>
[CmdletBinding()]
param(
    [string]$ClaudeHome,
    [string]$EnvelopeFile,
    [int]$TimeoutSec,
    [switch]$DryRun,
    # Test seam, honoured only alongside -DryRun (see Task 6). The spec names
    # four seams; this is a fifth the plan adds, because the spec's own Testing
    # section asks for the pre-spawn guard to be covered and no external input
    # can otherwise make that guard trip.
    [string]$InjectEnvKey
)

$ErrorActionPreference = 'Stop'

# -ClaudeHome exists so the test suite can point config, log, persona and
# scratch somewhere disposable. Without it every config and guard test would
# read the developer's live config - which seeds enabled:false, so each would
# exit 1 at the gate before reaching the behaviour under test - and would append
# rows to the real log the cost calibration reads. A child pwsh with USERPROFILE
# overridden is not a substitute: $HOME and ~ resolve once in PowerShell and do
# not follow a mid-process change.
$claudeHome = if ($ClaudeHome) { $ClaudeHome } else { Join-Path $HOME '.claude' }
$configPath  = Join-Path $claudeHome 'advisor-bridge.json'
$personaPath = Join-Path $claudeHome 'advisor-bridge-persona.md'
$logPath     = Join-Path $claudeHome 'advisor-bridge.log.jsonl'
$scratchDir  = Join-Path $claudeHome 'advisor-bridge-scratch'

function Fail([string]$message, [int]$code = 1) {
    [Console]::Error.WriteLine("advisor-bridge: $message")
    exit $code
}

# -as, not a cast: a non-numeric or negative value in user-editable JSON would
# otherwise throw a terminating error under $ErrorActionPreference = 'Stop' and
# take the wrapper down before it could say what was wrong with the config.
# Same reasoning as ollama-worker.ps1:128-133.
function Get-PositiveInt($value, [int]$default) {
    $n = $value -as [int]
    if ($null -eq $n -or $n -le 0) { return $default }
    return $n
}

# --- 1. Read config --------------------------------------------------------
$cfgRaw = $null
if (Test-Path -LiteralPath $configPath) {
    try { $cfgRaw = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json }
    catch { $cfgRaw = $null }
}

# --- 2. Enabled gate -------------------------------------------------------
# Enforced by the mechanism, not by the skill's prose. A caller can invoke this
# on stale context after a compact; every other constraint here is checked
# rather than trusted for the same reason. A missing or unreadable file counts
# as disabled, so a broken config fails closed rather than spending money.
#
# -isnot [bool], not -ne $true: when $cfgRaw.enabled is an array, `-ne`
# array-filters instead of comparing, returning a (possibly empty) collection
# that `if` then coerces to false - so `{"enabled": []}` or
# `{"enabled": [true,false]}` would fail OPEN and spend money. `-isnot [bool]`
# does not array-filter: `@() -isnot [bool]` is `$true`, so the gate fires.
# This also closes `"enabled": "true"` and `"enabled": 1`, which is a
# deliberate tightening, not a regression - every real config path
# (advisor-bridge.example.json, and the `on` command writing through
# ConvertTo-Json) produces a genuine JSON boolean.
if ($null -eq $cfgRaw -or $cfgRaw.enabled -isnot [bool] -or -not $cfgRaw.enabled) {
    Fail "disabled or unreadable config: $configPath`n  Enable with: /advisor-bridge on"
}

$model              = if ($cfgRaw.model) { $cfgRaw.model } else { 'claude-fable-5-1' }
$charBudget         = Get-PositiveInt $cfgRaw.charBudget 80000
$maxToolResultChars = Get-PositiveInt $cfgRaw.maxToolResultChars 2000
$timeoutSeconds     = if ($PSBoundParameters.ContainsKey('TimeoutSec')) {
                          Get-PositiveInt $TimeoutSec 240
                      } else {
                          Get-PositiveInt $cfgRaw.timeoutSec 240
                      }

# --- 5. Locate the caller's transcript -------------------------------------
# Glob, rather than recomputing Claude Code's cwd-to-directory-name mangling
# (C:\Users\<you>\... -> C--Users-<you>-...). That rule is undocumented, and
# reimplementing it buys nothing a glob does not already give while its failure
# mode is a wrong-or-missing file rather than an error.
#
# The base is the CALLER's config dir, not $claudeHome: -ClaudeHome redirects
# this script's own files, whereas the transcript belongs to whichever session
# invoked us. Ollama sessions do not set CLAUDE_CONFIG_DIR today, but honouring
# it costs one line and ignoring it would be a wrong-file failure, not an error.
$sessionId = $env:CLAUDE_CODE_SESSION_ID
if (-not $sessionId) {
    Fail "CLAUDE_CODE_SESSION_ID is not set - this script must run inside a Claude Code session, not from a bare shell."
}

$callerBase = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $HOME '.claude' }
# `Get-Item -Path` treats its whole argument as a wildcard pattern, not a
# literal path - so an unescaped session id or base directory containing
# `*`, `?` or `[` would silently glob-match (resolving a DIFFERENT session's
# transcript with exit 0) or silently miss (a legitimate transcript under a
# bracketed directory name). WildcardPattern.Escape neutralises metacharacters
# in the two interpolated components while leaving the literal `*` directory
# segment as a real wildcard.
$wc         = [System.Management.Automation.WildcardPattern]
$pattern    = Join-Path $wc::Escape($callerBase) 'projects' '*' "$($wc::Escape($sessionId)).jsonl"
$found      = @(Get-Item -Path $pattern -ErrorAction SilentlyContinue)

if ($found.Count -eq 0) {
    Fail "no transcript for session $sessionId under $(Join-Path $callerBase 'projects')\*\`n  The session may not have been written yet; send one message and retry."
}
if ($found.Count -gt 1) {
    $list = ($found | ForEach-Object { "    $($_.FullName)" }) -join "`n"
    Fail "session id $sessionId matches $($found.Count) transcripts:`n$list`n  Rendering the wrong one would advise on someone else's session. Delete or move`n  the stale copy, or set CLAUDE_CONFIG_DIR to disambiguate."
}
$transcriptPath = $found[0].FullName

# --- 6. Render -------------------------------------------------------------
# Line by line, each line in its own try/catch, and the file opened share-read.
# Both halves are load-bearing: the caller's own Claude Code process is
# appending to this file while we read it, so the last line is routinely a
# partial record. Under $ErrorActionPreference = 'Stop' an unguarded
# ConvertFrom-Json on it is a terminating error that would kill the wrapper
# before any log row and with an exit code outside the published table. Same
# hazard, same remedy, as ollama-worker.ps1:412-424.
function Read-Turns([string]$path) {
    $turns   = [System.Collections.Generic.List[object]]::new()
    $skipped = 0
    $fs      = [System.IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
    $reader  = [System.IO.StreamReader]::new($fs)
    try {
        while ($null -ne ($line = $reader.ReadLine())) {
            if (-not $line.Trim()) { continue }
            $rec = $null
            try { $rec = $line | ConvertFrom-Json } catch { $skipped++; continue }

            if ($rec.type -notin 'user', 'assistant') { continue }
            # -eq $true, not -ne $false: a record that omits the field
            # entirely is a main-agent record and must be kept. (-ne $true
            # would also array-filter instead of comparing if isSidechain
            # were ever array-valued - the same hazard that made the enabled
            # gate in Task 2 fail OPEN on `{"enabled": []}` - so do not
            # "simplify" this back to -ne.)
            if ($rec.isSidechain -eq $true) { continue }

            $turns.Add($rec)
        }
    }
    finally { $reader.Dispose(); $fs.Dispose() }
    return @{ Turns = $turns; Skipped = $skipped }
}

function Limit-Text([string]$s, [int]$max) {
    if ($null -eq $s) { return '' }
    if ($s.Length -le $max) { return $s }
    return $s.Substring(0, $max) + ' [truncated]'
}

function Format-Block($block, [int]$maxToolResult) {
    switch ($block.type) {
        'text'        { return $block.text }
        'thinking'    { return "[thinking] " + (Limit-Text $block.thinking 600) }
        'tool_use'    {
            $input = $block.input | ConvertTo-Json -Depth 6 -Compress
            return "[tool_use] $($block.name) " + (Limit-Text $input 800)
        }
        'tool_result' {
            $content = if ($block.content -is [string]) { $block.content }
                       else { $block.content | ConvertTo-Json -Depth 6 -Compress }
            return "[tool_result] " + (Limit-Text $content $maxToolResult)
        }
        default       { return '' }
    }
}

function Format-Turn($rec, [int]$maxToolResult) {
    # message.content is EITHER a block array OR a bare string - Claude Code
    # writes plain-string content for ordinary typed user messages, which is
    # exactly the shape of the first user message the whole budget sequence
    # exists to preserve. Wrapping a string in @() yields a one-element array
    # whose element has no .type, so Format-Block's switch would fall to
    # `default` and return '' - the turn would render as a header with an empty
    # body, silently. (Task 1 Step 5's measurement code branches on this same
    # distinction, which is where the shape is confirmed to exist.)
    if ($rec.message.content -is [string]) {
        return "--- $($rec.type) ---`n$($rec.message.content)"
    }
    $blocks = @($rec.message.content)
    $parts  = foreach ($b in $blocks) { Format-Block $b $maxToolResult }
    $body   = ($parts | Where-Object { $_ }) -join "`n"
    return "--- $($rec.type) ---`n$body"
}

$read     = Read-Turns $transcriptPath
$allTurns = @($read.Turns)
$skipped  = $read.Skipped

# --- 7. Non-empty check ----------------------------------------------------
# A full-price call over an empty render returns confident advice about nothing.
if ($allTurns.Count -eq 0) {
    Fail "no user or assistant turns survived the filters in $transcriptPath`n  Nothing to advise on."
}

function New-Header([int]$total, [int]$elided, [int]$skippedLines) {
    $branch = try { (& git rev-parse --abbrev-ref HEAD 2>$null) } catch { $null }
    if (-not $branch) { $branch = '(not a git repo)' }
    @(
        "cwd: $((Get-Location).Path)"
        "branch: $branch"
        "caller model: $($env:ANTHROPIC_DEFAULT_OPUS_MODEL ?? '(unknown)')"
        "turns: $total"
        "elided: $elided"
        "unparseable lines skipped: $skippedLines"
        ''
    ) -join "`n"
}

# --- -DryRun (minimal) -----------------------------------------------------
# A deliverable seam, not a test-only afterthought, and it belongs in THIS task:
# every assertion in Render.Tests.ps1 reads this JSON. It is deliberately
# outside the stdout/exit contract - it prints JSON and exits 0 without
# spawning, which is not "advice returned" in the sense of the exit table.
#
# Task 5 replaces the body with the budgeted values; Task 6 moves the block
# below the environment build and adds env, args, cwd and exe. Until then it
# reports only what the renderer itself knows, with turns_elided fixed at 0
# because nothing elides yet.
if ($DryRun) {
    $rendered = (New-Header $allTurns.Count 0 $skipped) +
                (($allTurns | ForEach-Object { Format-Turn $_ $maxToolResultChars }) -join "`n`n")
    [ordered]@{
        render         = $rendered
        chars_sent     = $rendered.Length
        turns_rendered = $allTurns.Count
        turns_elided   = 0
        lines_skipped  = $skipped
    } | ConvertTo-Json -Depth 8
    exit 0
}
